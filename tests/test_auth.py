"""Members-only auth (signup/login/me) and the per-member token budget.

No network or real SECRET_KEY (the `secret_key` fixture generates one). The budget runs through a
throwaway app calling a stub model, so "429 and no model call" is asserted on the stub's call record.
"""
import secrets

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.auth import ALGORITHM, create_access_token
from app.auth.budget import record_usage, require_budget
from app.db.db import get_db
from app.db.migrate_db import engine as sync_engine
from tests.conftest import signup_member

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    key = secrets.token_hex(32)
    monkeypatch.setenv("SECRET_KEY", key)
    return key


def signup(client, email="grower@example.com", password=PASSWORD, **extra):
    return signup_member(client, email, password, **extra)


def login_token(client, email="grower@example.com", password=PASSWORD):
    response = client.post("/members/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def sql(statement, **params):
    with sync_engine.begin() as connection:
        return connection.execute(text(statement), params)


# --- /members/me -------------------------------------------------------------

def test_me_without_token_is_401(client):
    assert client.get("/members/me").status_code == 401


def test_me_with_garbage_token_is_401(client):
    assert client.get("/members/me", headers=auth("not.a.jwt")).status_code == 401


def test_me_with_valid_token_returns_that_member(client):
    created = signup(client, display_name="Kim").json()
    body = client.get("/members/me", headers=auth(login_token(client))).json()
    assert body["id"] == created["id"]
    assert body["email"] == "grower@example.com"
    assert body["display_name"] == "Kim"
    assert body["tokens_used_today"] == 0
    assert body["tokens_budget_daily"] == 20000
    assert "password_hash" not in body and "password" not in body


def test_expired_token_is_401(client, secret_key):
    member_id = signup(client).json()["id"]
    token = create_access_token(member_id, expires_minutes=-1)
    assert client.get("/members/me", headers=auth(token)).status_code == 401


def test_token_signed_with_another_key_is_401(client):
    member_id = signup(client).json()["id"]
    forged = jwt.encode({"sub": str(member_id)}, secrets.token_hex(32), algorithm=ALGORITHM)
    assert client.get("/members/me", headers=auth(forged)).status_code == 401


def test_token_for_a_deleted_member_is_401(client):
    member_id = signup(client).json()["id"]
    token = login_token(client)
    sql("DELETE FROM members WHERE id = :id", id=member_id)
    assert client.get("/members/me", headers=auth(token)).status_code == 401


# --- who needs a token -------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/main-categories", "/sub-categories", "/crops", "/items"])
def test_existing_endpoints_work_without_a_token(client, path):
    assert client.get(path).status_code == 200


def test_only_the_expected_routes_require_a_token():
    # every route declaring the bearer scheme has a `security` entry in OpenAPI, so this pins which
    # routes are guarded (CMS writes included) and fails when one is added or removed.
    from app.main import app

    guarded = sorted(
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
        if operation.get("security")
    )
    assert guarded == [
        "DELETE /items/{item_id}",
        "DELETE /main-categories/{main_category_id}",
        "DELETE /members/invites/{invite_id}",
        "DELETE /members/{member_id}",
        "DELETE /sub-categories/{sub_category_id}",
        "GET /items",
        "GET /members",
        "GET /members/invites",
        "GET /members/me",
        "PATCH /members/{member_id}",
        "POST /chat",
        "POST /items",
        "POST /main-categories",
        "POST /members/invites",
        "POST /members/{member_id}/unlock",
        "POST /sub-categories",
    ]


# --- duplicate signup, indistinguishable login failures ----------------------

def test_duplicate_signup_is_400(client):
    assert signup(client).status_code == 201
    assert signup(client).status_code == 400
    # Email is normalised, so a case variant is the same member.
    assert signup(client, email="Grower@Example.com").status_code == 400


def test_wrong_password_and_unknown_email_get_identical_401(client):
    signup(client)
    wrong = client.post("/members/login", json={"email": "grower@example.com", "password": "nope-nope-nope"})
    unknown = client.post("/members/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


# --- argon2 at rest ----------------------------------------------------------

def test_stored_password_is_an_argon2_hash(client):
    signup(client)
    stored = sql("SELECT password_hash FROM members").scalar_one()
    assert stored.startswith("$argon2id$")
    assert stored != PASSWORD and PASSWORD not in stored


def test_signup_response_never_contains_the_hash(client):
    assert "password" not in signup(client).text


# --- the budget --------------------------------------------------------------

class StubModel:
    """Stands in for the Anthropic client; counts calls, reports usage."""

    def __init__(self, tokens=100):
        self.calls = 0
        self.tokens = tokens

    def __call__(self):
        self.calls += 1
        return self.tokens


@pytest.fixture
def budget_app():
    """(client, stub): a throwaway app with one route shaped like POST /chat
    (require_budget, model, record_usage)."""
    stub = StubModel()
    app = FastAPI()

    @app.post("/fake-chat")
    async def fake_chat(member=Depends(require_budget), db=Depends(get_db)):
        used = stub()
        await record_usage(db, member.id, used)
        return {"used": used}

    return TestClient(app, raise_server_exceptions=False), stub


@pytest.fixture
def member(client):
    member_id = signup(client).json()["id"]
    return member_id, auth(login_token(client))


def usage_row(member_id):
    return sql(
        "SELECT tokens_used_today, budget_window_start = CURRENT_DATE AS is_today "
        "FROM members WHERE id = :id", id=member_id,
    ).one()


def test_budget_route_needs_a_token(budget_app):
    client, stub = budget_app
    assert client.post("/fake-chat").status_code == 401
    assert stub.calls == 0


def test_under_budget_member_is_served_and_actual_usage_is_added(budget_app, member):
    client, stub = budget_app
    member_id, headers = member
    assert client.post("/fake-chat", headers=headers).status_code == 200
    assert client.post("/fake-chat", headers=headers).status_code == 200
    assert stub.calls == 2
    assert usage_row(member_id).tokens_used_today == 200


@pytest.mark.parametrize("used", [20000, 25000])
def test_member_at_or_over_budget_gets_429_and_no_model_call(budget_app, member, used):
    client, stub = budget_app
    member_id, headers = member
    sql("UPDATE members SET tokens_used_today = :u WHERE id = :id", u=used, id=member_id)
    response = client.post("/fake-chat", headers=headers)
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 86400
    assert stub.calls == 0
    assert usage_row(member_id).tokens_used_today == used  # untouched


def test_budget_is_per_member(budget_app, client, member):
    app_client, stub = budget_app
    member_id, headers = member
    sql("UPDATE members SET tokens_used_today = tokens_budget_daily WHERE id = :id", id=member_id)
    signup(client, email="other@example.com")
    other = auth(login_token(client, email="other@example.com"))
    assert app_client.post("/fake-chat", headers=headers).status_code == 429
    assert app_client.post("/fake-chat", headers=other).status_code == 200
    assert stub.calls == 1


def test_per_member_budget_can_be_raised_without_a_redeploy(budget_app, member):
    client, stub = budget_app
    member_id, headers = member
    sql("UPDATE members SET tokens_used_today = 20000 WHERE id = :id", id=member_id)
    assert client.post("/fake-chat", headers=headers).status_code == 429
    sql("UPDATE members SET tokens_budget_daily = 50000 WHERE id = :id", id=member_id)
    assert client.post("/fake-chat", headers=headers).status_code == 200


def test_member_whose_window_is_yesterday_is_reset_and_served(budget_app, member):
    client, stub = budget_app
    member_id, headers = member
    sql(
        "UPDATE members SET tokens_used_today = 20000, "
        "budget_window_start = CURRENT_DATE - 1 WHERE id = :id", id=member_id,
    )
    assert client.post("/fake-chat", headers=headers).status_code == 200
    assert stub.calls == 1
    row = usage_row(member_id)
    assert row.is_today
    assert row.tokens_used_today == 100  # reset to 0, then this call's usage


def test_require_budget_returns_the_member_after_the_reset(client):
    member_id = signup(client).json()["id"]
    headers = auth(login_token(client))
    sql(
        "UPDATE members SET tokens_used_today = 20000, "
        "budget_window_start = CURRENT_DATE - 1 WHERE id = :id", id=member_id,
    )
    app = FastAPI()

    @app.get("/probe")
    async def probe(member=Depends(require_budget)):
        return {"used": member.tokens_used_today}

    response = TestClient(app).get("/probe", headers=headers)
    assert response.json() == {"used": 0}


def test_record_usage_is_an_atomic_increment(budget_app, member):
    # two requests' usage added back to back must both land: the UPDATE adds, not a Python-side sum.
    client, stub = budget_app
    member_id, headers = member
    stub.tokens = 7
    for _ in range(5):
        assert client.post("/fake-chat", headers=headers).status_code == 200
    assert usage_row(member_id).tokens_used_today == 35


def test_negative_usage_is_refused_by_the_database_and_the_function(member):
    member_id, _ = member
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        sql("UPDATE members SET tokens_used_today = -1 WHERE id = :id", id=member_id)
    import asyncio
    from app.db.db import async_session

    async def go():
        async with async_session() as db:
            await record_usage(db, member_id, -5)

    with pytest.raises(ValueError):
        asyncio.run(go())


# --- no SECRET_KEY needed to import, loud when signing -----------------------

def test_missing_secret_key_fails_loudly_when_signing(monkeypatch):
    monkeypatch.delenv("SECRET_KEY")
    with pytest.raises(KeyError):
        create_access_token(1)


def test_empty_secret_key_fails_loudly_too(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "")
    with pytest.raises(KeyError):
        create_access_token(1)


def test_unknown_email_login_still_runs_a_password_verification(client, monkeypatch):
    # timing side channel: skipping argon2 for an unknown email answers ~10x faster than a wrong
    # password, revealing which emails are registered.
    import app.router.member as member_router

    seen = []
    real = member_router.verify_password

    async def spy(plain, hashed):
        seen.append(hashed)
        return await real(plain, hashed)

    monkeypatch.setattr(member_router, "verify_password", spy)
    response = client.post("/members/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert response.status_code == 401
    assert len(seen) == 1 and seen[0].startswith("$argon2id$")


def test_default_token_lifetime_is_30_minutes(client):
    token = create_access_token(1)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 30 * 60


def test_hashing_and_verifying_go_through_the_threadpool(monkeypatch):
    import asyncio
    import app.auth.auth as auth_module

    offloaded = []
    real = auth_module.run_in_threadpool

    async def spy(func, *args):
        offloaded.append(func.__name__)
        return await real(func, *args)

    monkeypatch.setattr(auth_module, "run_in_threadpool", spy)

    async def go():
        hashed = await auth_module.hash_password(PASSWORD)
        assert await auth_module.verify_password(PASSWORD, hashed)

    asyncio.run(go())
    assert offloaded == ["hash", "verify"]


# --- TOKENS_BUDGET_DAILY seeds new members only ------------------------------

def test_signup_gets_the_configured_starting_budget(client, monkeypatch):
    import app.crud.member as member_crud

    first = signup(client, email="first@example.com").json()["id"]
    monkeypatch.setattr(member_crud, "TOKENS_BUDGET_DAILY", 1234)
    second = signup(client, email="second@example.com")
    assert second.status_code == 201, second.text
    budgets = {
        row.id: row.tokens_budget_daily
        for row in sql("SELECT id, tokens_budget_daily FROM members")
    }
    assert budgets[second.json()["id"]] == 1234
    assert budgets[first] == 20000  # an existing member keeps theirs


def test_negative_budget_setting_fails_loudly_at_import():
    from app.crud.member import _read_budget

    assert _read_budget("0") == 0
    with pytest.raises(ValueError, match="TOKENS_BUDGET_DAILY"):
        _read_budget("-5")
