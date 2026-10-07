"""POST /members/{id}/unlock: an admin clears an account's login throttle (audited, one transaction)."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.crud.audit as audit_crud
from app.auth.account_key import throttle_key
from app.auth.auth import create_access_token
from app.db import db as app_db
from app.model.model import Member, MemberAuditEvent
from app.router.member import unlock_member
from tests.conftest import DEFAULT_PASSWORD, add_member, audit_rows, signup_member, sql

EMAIL = "grower@example.com"


def _lock(client, *spellings):
    """Ten wrong logins cycling through the spellings: one account's counter."""
    for i in range(10):
        spelling = spellings[i % len(spellings)]
        assert client.post("/members/login", json={"email": spelling, "password": "wrong"}).status_code == 401


def _throttle_keys():
    return {k for (k,) in sql("SELECT email_key FROM login_throttle").all()}


def _call_unlock(actor_id, target_id):
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            actor = await session.get(Member, actor_id)
            try:
                await unlock_member(target_id, actor, session)
                return 204
            except HTTPException as error:
                return error.status_code

    try:
        return asyncio.run(asyncio.wait_for(scenario(), 30))
    finally:
        asyncio.run(engine.dispose())


# u1
def test_no_token_is_401(client):
    assert client.post("/members/1/unlock").status_code == 401


@pytest.mark.parametrize("role", ["member", "editor"])
def test_member_and_editor_are_403_and_change_nothing(client_with_role, client, role):
    signup_member(client)
    _lock(client, EMAIL)
    caller = client_with_role(role)
    target = sql("SELECT id FROM members WHERE email = :e", e=EMAIL).scalar_one()
    assert caller.post(f"/members/{target}/unlock").status_code == 403
    assert len(_throttle_keys()) == 1 and audit_rows() == []


def test_admin_is_204(client_with_role):
    admin = client_with_role("admin")
    target = add_member("member")
    response = admin.post(f"/members/{target}/unlock")
    assert response.status_code == 204 and response.content == b""


@pytest.mark.parametrize("path_id", ["0", "-1", "abc", str(2**31)])
def test_a_bad_id_is_422(client_with_role, path_id):
    assert client_with_role("admin").post(f"/members/{path_id}/unlock").status_code == 422


# u2
def test_an_unknown_id_is_404_with_no_audit_row(client_with_role):
    assert client_with_role("admin").post("/members/99999/unlock").status_code == 404
    assert audit_rows() == []


def test_a_stale_actor_gets_403_not_404_for_a_missing_id():
    actor = add_member("admin", is_active=False)
    assert _call_unlock(actor, 99999) == 403
    assert audit_rows() == []


# u2b
def test_unlocking_with_no_throttle_row_is_204_and_every_call_is_audited(client_with_role):
    admin = client_with_role("admin")
    target = add_member("member")
    for expected in (1, 2):
        assert admin.post(f"/members/{target}/unlock").status_code == 204
        assert len(audit_rows()) == expected
    assert audit_rows() == [(admin.member_id, "unlock", target, {"cleared": 0})] * 2


# u3, u4
def test_unlock_after_ten_mixed_case_failures_lets_the_correct_password_in(client, client_with_role):
    signup_member(client, email="grower@x.com")
    _lock(client, " Grower@X.com ", "grower@x.com", "GROWER@x.com ")
    login = {"email": "grower@x.com", "password": DEFAULT_PASSWORD}
    assert client.post("/members/login", json=login).status_code == 429
    target = sql("SELECT id FROM members WHERE email = 'grower@x.com'").scalar_one()
    admin = client_with_role("admin")
    assert admin.post(f"/members/{target}/unlock").status_code == 204
    assert client.post("/members/login", json=login).status_code == 200
    assert _throttle_keys() == set()
    assert admin.post(f"/members/{target}/unlock").status_code == 204  # u4: idempotent
    assert [row[3] for row in audit_rows()] == [{"cleared": 1}, {"cleared": 0}]


def test_unlock_leaves_other_accounts_throttle_rows_alone(client, client_with_role):
    signup_member(client)
    add_member("member", email="other@example.com")
    for _ in range(3):
        client.post("/members/login", json={"email": "other@example.com", "password": "wrong"})
        client.post("/members/login", json={"email": EMAIL, "password": "wrong"})
    target = sql("SELECT id FROM members WHERE email = :e", e=EMAIL).scalar_one()
    assert client_with_role("admin").post(f"/members/{target}/unlock").status_code == 204
    assert _throttle_keys() == {throttle_key("other@example.com")}
    assert sql("SELECT attempts FROM login_throttle").scalar_one() == 3


# u5
def test_the_audit_row_has_the_target_and_no_email_or_key(client, client_with_role):
    signup_member(client)
    client.post("/members/login", json={"email": EMAIL, "password": "wrong"})
    target = sql("SELECT id FROM members WHERE email = :e", e=EMAIL).scalar_one()
    admin = client_with_role("admin")
    assert admin.post(f"/members/{target}/unlock").status_code == 204
    rows = audit_rows()
    assert rows == [(admin.member_id, "unlock", target, {"cleared": 1})]
    everything = json.dumps([list(row) for row in rows])
    assert EMAIL not in everything and "example.com" not in everything and throttle_key(EMAIL) not in everything


# u5b
def test_a_failing_audit_helper_is_500_and_the_throttle_row_stays(client, client_with_role, monkeypatch):
    signup_member(client)
    client.post("/members/login", json={"email": EMAIL, "password": "wrong"})
    target = sql("SELECT id FROM members WHERE email = :e", e=EMAIL).scalar_one()
    admin = client_with_role("admin")

    async def broken(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(audit_crud, "record_member_unlock", broken)
    assert admin.post(f"/members/{target}/unlock").status_code == 500
    assert _throttle_keys() == {throttle_key(EMAIL)}
    assert audit_rows() == []


def test_if_the_database_rejects_the_audit_row_the_throttle_row_stays(client, client_with_role, monkeypatch):
    signup_member(client)
    client.post("/members/login", json={"email": EMAIL, "password": "wrong"})
    target = sql("SELECT id FROM members WHERE email = :e", e=EMAIL).scalar_one()
    admin = client_with_role("admin")

    async def bad_row(db, actor_id, target_id, cleared):
        db.add(MemberAuditEvent(actor_id=actor_id, action=None, target_id=target_id, detail={}))

    monkeypatch.setattr(audit_crud, "record_member_unlock", bad_row)
    assert admin.post(f"/members/{target}/unlock").status_code == 500
    assert _throttle_keys() == {throttle_key(EMAIL)}
    assert audit_rows() == []


# u6
def test_an_admin_locked_out_of_login_unlocks_themselves_with_a_token_issued_before(client, monkeypatch):
    # Only a token younger than ACCESS_TOKEN_EXPIRE_MINUTES (30). An expired token has no in-band
    # recovery; that is the operator script's case, not covered here.
    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    signup_member(client, email="boss@example.com")
    boss = sql("SELECT id FROM members WHERE email = 'boss@example.com'").scalar_one()
    sql("UPDATE members SET role = 'admin' WHERE id = :id", id=boss)
    token = create_access_token(boss)
    _lock(client, " Boss@Example.com ", "boss@example.com")
    login = {"email": "boss@example.com", "password": DEFAULT_PASSWORD}
    assert client.post("/members/login", json=login).status_code == 429
    response = client.post(f"/members/{boss}/unlock", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204
    assert client.post("/members/login", json=login).status_code == 200
