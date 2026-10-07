"""Signup needs an invite: POST /members/signup claims one atomically.

Tests of signup itself post directly; others use signup_member. Concurrency tests call the handler
or crud function in one event loop (concurrent TestClients in threads hang intermittently).
"""
import asyncio
import json
import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.router.member as member_router
from app.crud.invite import claim_invite
from app.db import db as app_db
from app.schema.member import MemberCreate
from tests.conftest import DEFAULT_PASSWORD, make_invite, signup_member, sql

URL = "/members/signup"
REFUSED = {"detail": "Invalid or expired invite"}


def post(client, code, email="grower@example.com", **extra):
    return client.post(
        URL,
        json={"email": email, "password": DEFAULT_PASSWORD, "invite_code": code, **extra},
    )


def member_count():
    return sql("SELECT count(*) FROM members").scalar_one()


def used_at():
    return sql("SELECT used_at FROM member_invites ORDER BY id").scalars().all()


def expired_code():
    code = make_invite()
    sql("UPDATE member_invites SET expires_at = now() - interval '1 second'")
    return code


# --- who gets in -------------------------------------------------------------

def test_a_valid_invite_admits_a_member_and_is_spent(client):
    code = make_invite()
    response = post(client, code)
    assert response.status_code == 201
    assert response.json()["role"] == "member"
    assert member_count() == 1
    assert used_at()[0] is not None


@pytest.mark.parametrize("invite_role", ["member", "editor"])
def test_the_new_member_takes_the_invites_role_and_me_shows_it(client, monkeypatch, invite_role):
    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    assert signup_member(client, invite_role=invite_role).status_code == 201
    token = client.post(
        "/members/login", json={"email": "grower@example.com", "password": DEFAULT_PASSWORD}
    ).json()["access_token"]
    me = client.get("/members/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == invite_role


def test_a_role_in_the_body_is_ignored(client):
    response = signup_member(client, role="admin")
    assert response.status_code == 201
    assert response.json()["role"] == "member"
    # ... and it does not outrank the invite either way.
    other = signup_member(client, email="e@example.com", role="member", invite_role="editor")
    assert other.json()["role"] == "editor"


def test_a_bound_invite_matches_the_email_in_any_letter_case(client):
    code = make_invite(email=" Mixed@Example.COM ")
    assert post(client, code, email="MIXED@example.com").status_code == 201
    assert sql("SELECT email FROM members").scalar_one() == "mixed@example.com"


# --- who is refused ----------------------------------------------------------

@pytest.mark.parametrize("body", [{}, {"invite_code": None}, {"invite_code": "x" * 19}])
def test_a_missing_or_short_code_is_422(client, body):
    payload = {"email": "grower@example.com", "password": DEFAULT_PASSWORD, **body}
    assert client.post(URL, json=payload).status_code == 422
    assert member_count() == 0


def used_code(client):
    code = make_invite()
    assert post(client, code, email="first@example.com").status_code == 201
    sql("DELETE FROM members")  # a used code stays used even if its member is gone
    return code


@pytest.mark.parametrize(
    "make_code",
    [
        lambda client: "u" * 43,
        used_code,
        lambda client: expired_code(),
        lambda client: make_invite(email="someone-else@example.com"),
    ],
    ids=["unknown", "used", "expired", "wrong-email"],
)
def test_every_bad_code_gets_the_same_400(client, make_code):
    response = post(client, make_code(client))
    assert response.status_code == 400
    assert response.json() == REFUSED
    assert member_count() == 0


def test_a_used_code_cannot_be_used_twice(client):
    code = make_invite()
    assert post(client, code, email="a@example.com").status_code == 201
    assert post(client, code, email="b@example.com").json() == REFUSED
    assert member_count() == 1


def test_a_hundred_bad_attempts_create_no_member_and_never_hash(client, monkeypatch):
    async def must_not_hash(plain):
        raise AssertionError("hash_password reached with a bad invite")

    monkeypatch.setattr(member_router, "hash_password", must_not_hash)
    bound = make_invite(email="someone-else@example.com")
    stale = expired_code()
    for i in range(100):
        code = [f"{i:02d}" + "z" * 41, bound, stale][i % 3]
        response = post(client, code, email=f"g{i}@example.com")
        assert response.status_code == 400, response.text
        assert response.json() == REFUSED
    assert member_count() == 0


def test_a_bad_invite_with_a_registered_email_does_not_say_so(client):
    assert signup_member(client).status_code == 201
    assert post(client, "q" * 43).json() == REFUSED


# --- a duplicate email must not burn the invite ------------------------------

def test_a_duplicate_email_is_400_and_leaves_the_invite_unused(client):
    assert signup_member(client).status_code == 201
    code = make_invite()
    duplicate = post(client, code, email="Grower@Example.com")
    assert duplicate.status_code == 400
    assert duplicate.json() == {"detail": "Email already registered"}
    assert used_at()[1] is None
    assert member_count() == 1
    # The same code still works for a fresh address.
    assert post(client, code, email="fresh@example.com").status_code == 201


def test_a_lost_race_on_the_unique_email_leaves_the_invite_unused(client, monkeypatch):
    """The pre-check passes (stubbed), so the UNIQUE constraint is what refuses."""
    assert signup_member(client).status_code == 201
    code = make_invite()

    async def nobody(db, email):
        return None

    monkeypatch.setattr(member_router.member_crud, "get_member_by_email", nobody)
    response = post(client, code)
    assert response.status_code == 400
    assert response.json() == {"detail": "Email already registered"}
    assert used_at()[1] is None
    assert member_count() == 1


# --- input bounds (Q8) -------------------------------------------------------

@pytest.mark.parametrize(
    "email",
    ["a\x00b@example.com", "a@b\x00.io", "a" * 252 + "@x.io"],
    ids=["nul-local", "nul-domain", "over-255"],
)
def test_a_nul_or_oversized_email_is_422_not_500(client, email):
    assert post(client, make_invite(), email=email).status_code == 422
    assert member_count() == 0


def test_an_oversized_password_is_422(client):
    code = make_invite()
    response = client.post(
        URL, json={"email": "a@example.com", "password": "p" * 129, "invite_code": code}
    )
    assert response.status_code == 422
    assert used_at()[0] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("invite_code", "\ud800" * 25),
        ("email", "a\ud800@example.com"),
        ("password", "p\ud800" * 8),
        ("display_name", "x\ud800"),
        ("display_name", "x\x00"),
    ],
    ids=["code-surrogate", "email-surrogate", "password-surrogate", "name-surrogate", "name-nul"],
)
def test_surrogates_and_a_nul_name_are_422_and_leave_the_invite_unused(client, field, value):
    code = make_invite()
    body = {"email": "a@example.com", "password": DEFAULT_PASSWORD, "invite_code": code}
    body[field] = value
    # ensure_ascii escapes the lone surrogate as \ud800, as a real client would send it.
    response = client.post(
        URL, content=json.dumps(body), headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert used_at()[0] is None
    assert member_count() == 0


def test_a_failing_create_member_leaves_the_invite_unused(client, monkeypatch):
    code = make_invite()

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(member_router.member_crud, "create_member", boom)
    assert post(client, code).status_code == 500
    assert used_at()[0] is None
    assert member_count() == 0


# --- concurrency -------------------------------------------------------------

def test_two_connections_race_for_one_invite_and_one_wins():
    """claim_invite on two connections: the second waits on the first's row lock, then finds the
    row used and gets nothing (without `used_at IS NULL` in the UPDATE it would claim it again)."""
    code = make_invite()
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario():
        async with AsyncSession(engine) as first, AsyncSession(engine) as second:
            assert await claim_invite(first, code, "a@example.com") == "member"
            waiting = asyncio.create_task(claim_invite(second, code, "b@example.com"))
            await asyncio.sleep(0.5)
            assert not waiting.done()  # blocked on the lock, not decided yet
            await first.commit()
            return await asyncio.wait_for(waiting, 10)

    try:
        assert asyncio.run(asyncio.wait_for(scenario(), 30)) is None
    finally:
        asyncio.run(engine.dispose())


def test_twenty_concurrent_signups_do_not_exhaust_the_pool(monkeypatch):
    """Each valid signup holds its invite's row lock and a pooled connection across hashing. 20 at
    once against the default pool (5 + 10) must all succeed: the surplus waits, none times out."""
    running = peak = 0

    async def slow_hash(plain):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.2)
        running -= 1
        return "stub-hash"

    monkeypatch.setattr(member_router, "hash_password", slow_hash)
    codes = [make_invite() for _ in range(20)]
    engine = create_async_engine(app_db.ASYNC_DB_URL)  # same default pool as the app's

    async def one(i):
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            payload = MemberCreate(
                email=f"m{i}@example.com", password=DEFAULT_PASSWORD, invite_code=codes[i]
            )
            return await member_router.signup(payload, session)

    async def scenario():
        return await asyncio.gather(*(one(i) for i in range(20)))

    started = time.monotonic()
    try:
        members = asyncio.run(asyncio.wait_for(scenario(), 60))
    finally:
        asyncio.run(engine.dispose())
    assert len(members) == 20
    assert member_count() == 20
    assert all(value is not None for value in used_at())
    assert peak > 5, "the test never went beyond the pool's first five connections"
    assert time.monotonic() - started < 15
