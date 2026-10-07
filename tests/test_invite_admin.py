"""Invites, admin side: GET /members/invites and DELETE /members/invites/{id}.

Role matrix with client_with_role; rows are read back and aged by SQL (creating is test_invites.py).
"""
import asyncio
import hashlib
import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.crud.member as member_crud
import app.db.db as app_db
from app.model.model import Member
from app.router.member import create_invite, revoke_invite
from app.schema.member import InviteCreate
from tests.conftest import add_member, audit_rows, make_invite, signup_member, sql

URL = "/members/invites"
BAD_CODE_BODY = {"detail": "Invalid or expired invite"}


def invite_ids():
    return [row.id for row in sql("SELECT id FROM member_invites ORDER BY id")]


def new_invite(role="member", email=None, **age):
    """Store an invite and return (id, code). `age` is 'used' or 'expired' = True."""
    code = make_invite(role=role, email=email)
    hashed = hashlib.sha256(code.encode()).hexdigest()
    if age.get("used"):
        sql("UPDATE member_invites SET used_at = now() WHERE code_hash = :h", h=hashed)
    if age.get("expired"):
        sql("UPDATE member_invites SET expires_at = now() - interval '1 hour' WHERE code_hash = :h", h=hashed)
    return sql("SELECT id FROM member_invites WHERE code_hash = :h", h=hashed).scalar_one(), code


# --- who may list and revoke -------------------------------------------------

def test_no_token_is_401_for_both(client):
    invite_id, _ = new_invite()
    assert client.get(URL).status_code == 401
    assert client.delete(f"{URL}/{invite_id}").status_code == 401
    assert invite_ids() == [invite_id]
    assert audit_rows() == []


@pytest.mark.parametrize("role", ["member", "editor"])
def test_member_and_editor_are_403_for_both(client_with_role, role):
    invite_id, _ = new_invite()
    caller = client_with_role(role)
    assert caller.get(URL).status_code == 403
    assert caller.delete(f"{URL}/{invite_id}").status_code == 403
    # 403 before 404 and before the path's 422: a refused caller learns nothing.
    assert caller.delete(f"{URL}/999999").status_code == 403
    assert caller.delete(f"{URL}/0").status_code == 403
    assert invite_ids() == [invite_id]
    assert audit_rows() == []


# --- list --------------------------------------------------------------------

def test_list_is_empty_when_there_are_no_invites(client_with_role):
    assert client_with_role("admin").get(URL).json() == []


def test_list_shows_only_open_invites_and_never_the_code_or_hash(client_with_role):
    open_id, open_code = new_invite(role="editor", email="Bound@Example.com")
    used_id, used_code = new_invite(used=True)
    expired_id, expired_code = new_invite(expired=True)
    other_id, other_code = new_invite()
    response = client_with_role("admin").get(URL)
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [open_id, other_id]  # by id; used and expired absent
    assert set(body[0]) == {"id", "role", "email", "created_at", "expires_at"}
    assert (body[0]["role"], body[0]["email"]) == ("editor", "bound@example.com")
    assert body[1]["email"] is None
    for secret in (open_code, used_code, expired_code, other_code):
        assert secret not in response.text
        assert hashlib.sha256(secret.encode()).hexdigest() not in response.text
    assert "code" not in response.text.replace("expires", "")  # no code/code_hash key at all


def test_list_pagination(client_with_role):
    ids = [new_invite()[0] for _ in range(5)]
    admin = client_with_role("admin")
    assert [i["id"] for i in admin.get(URL, params={"limit": 2}).json()] == ids[:2]
    assert [i["id"] for i in admin.get(URL, params={"limit": 2, "offset": 3}).json()] == ids[3:]
    assert admin.get(URL, params={"offset": 99}).json() == []


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"limit": "x"}, {"offset": -1}, {"offset": 2**63}],
)
def test_list_bad_paging_is_422(client_with_role, params):
    assert client_with_role("admin").get(URL, params=params).status_code == 422


def test_list_is_not_shadowed_by_the_member_id_route(client_with_role):
    # "invites" must not be read as a member id (that would be a 422).
    assert client_with_role("admin").get(URL).status_code == 200


# --- revoke ------------------------------------------------------------------

def test_revoke_hard_deletes_the_row_and_audits_it(client_with_role):
    keep_id, _ = new_invite()
    invite_id, code = new_invite(role="editor", email="who@example.com")
    admin = client_with_role("admin")
    response = admin.delete(f"{URL}/{invite_id}")
    assert (response.status_code, response.content) == (204, b"")
    assert invite_ids() == [keep_id]  # the row is gone, not flagged
    assert [i["id"] for i in admin.get(URL).json()] == [keep_id]
    ((actor_id, action, target_id, detail),) = audit_rows()
    assert (actor_id, action, target_id) == (admin.member_id, "invite_revoke", invite_id)
    assert detail == {"role": "editor"}
    text = str(audit_rows()).lower()
    assert code not in text and hashlib.sha256(code.encode()).hexdigest() not in text
    assert "who@example.com" not in text


def test_a_second_revoke_and_an_unknown_id_are_404_with_no_audit_row(client_with_role):
    invite_id, _ = new_invite()
    admin = client_with_role("admin")
    assert admin.delete(f"{URL}/{invite_id}").status_code == 204
    assert admin.delete(f"{URL}/{invite_id}").status_code == 404
    assert admin.delete(f"{URL}/{invite_id + 1000}").status_code == 404
    assert len(audit_rows()) == 1


def test_a_used_invite_is_409_and_stays_with_no_audit_row(client_with_role):
    invite_id, _ = new_invite(used=True)
    response = client_with_role("admin").delete(f"{URL}/{invite_id}")
    assert response.status_code == 409
    assert invite_ids() == [invite_id]
    assert audit_rows() == []


def test_an_expired_unused_invite_can_be_revoked(client_with_role):
    invite_id, _ = new_invite(expired=True)
    assert client_with_role("admin").delete(f"{URL}/{invite_id}").status_code == 204
    assert invite_ids() == []
    assert len(audit_rows()) == 1


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", str(2**31), str(2**63), str(2**64)])
def test_a_bad_or_huge_id_is_422_not_500(client_with_role, bad):
    response = client_with_role("admin").delete(f"{URL}/{bad}")
    assert response.status_code == 422
    assert audit_rows() == []


def test_the_largest_valid_id_is_404_not_500(client_with_role):
    assert client_with_role("admin").delete(f"{URL}/{2**31 - 1}").status_code == 404


def test_revoke_does_not_collide_with_the_member_delete_route(client_with_role):
    admin = client_with_role("admin")
    victim = add_member("member")
    invite_id, _ = new_invite()
    assert admin.delete(f"{URL}/{invite_id}").status_code == 204
    assert sql("SELECT count(*) FROM members WHERE id = :i", i=victim).scalar_one() == 1
    # No id: this is DELETE /members/{member_id} with member_id="invites".
    assert admin.delete(URL).status_code == 422
    assert admin.delete(f"/members/{victim}").status_code == 204
    assert [row[1] for row in audit_rows()] == ["invite_revoke", "delete"]


def test_a_revoked_code_fails_signup_with_the_common_400(client, client_with_role):
    invite_id, code = new_invite()
    assert client_with_role("admin").delete(f"{URL}/{invite_id}").status_code == 204
    revoked = signup_member(client, email="late@example.com", invite_code=code)
    unknown = signup_member(client, email="late@example.com", invite_code=secrets.token_urlsafe(32))
    assert revoked.status_code == 400
    assert revoked.json() == BAD_CODE_BODY == unknown.json()
    assert sql("SELECT count(*) FROM members WHERE email = 'late@example.com'").scalar_one() == 0


def test_a_code_used_at_signup_cannot_then_be_revoked(client, client_with_role):
    invite_id, code = new_invite()
    assert signup_member(client, email="in@example.com", invite_code=code).status_code == 201
    assert client_with_role("admin").delete(f"{URL}/{invite_id}").status_code == 409


def test_if_the_audit_insert_fails_the_invite_is_not_deleted(client_with_role, monkeypatch):
    import app.crud.audit as audit_crud

    async def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    invite_id, code = new_invite()
    monkeypatch.setattr(audit_crud, "record_invite_revoke", boom)
    assert client_with_role("admin").delete(f"{URL}/{invite_id}").status_code == 500
    assert invite_ids() == [invite_id]
    assert audit_rows() == []
    monkeypatch.undo()
    # And the code still works.
    assert signup_member(client_with_role("member"), email="x@example.com", invite_code=code).status_code == 201


def _revoke_as_loaded_actor(actor_id, invite_id):
    """revoke_invite called directly with the actor loaded now, to model a request whose
    actor was demoted or deactivated after the guard passed (the guard is not run)."""
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            actor = await session.get(Member, actor_id)
            # The guard has passed; now the actor loses the role (a concurrent request).
            sql("UPDATE members SET role = 'member' WHERE id = :i", i=actor_id)
            try:
                await revoke_invite(invite_id, actor, session)
                return 204
            except HTTPException as error:
                return error.status_code

    try:
        return asyncio.run(asyncio.wait_for(scenario(), 30))
    finally:
        asyncio.run(engine.dispose())


def test_an_actor_demoted_after_the_guard_is_403_even_for_an_unknown_id():
    actor = add_member("admin")
    invite_id, _ = new_invite()
    assert _revoke_as_loaded_actor(actor, invite_id + 1000) == 403  # 403, not 404
    assert invite_ids() == [invite_id]


def test_an_actor_demoted_after_the_guard_cannot_revoke():
    actor = add_member("admin")
    invite_id, _ = new_invite()
    assert _revoke_as_loaded_actor(actor, invite_id) == 403
    assert invite_ids() == [invite_id]
    assert audit_rows() == []


def _create_as_loaded_actor(actor_id):
    """create_invite called directly with the actor loaded now, then demoted by SQL
    (the guard is not run): a request whose actor lost the role after the guard passed."""
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            actor = await session.get(Member, actor_id)
            sql("UPDATE members SET role = 'member' WHERE id = :i", i=actor_id)
            try:
                await create_invite(InviteCreate(role="editor"), actor, session)
                return 201
            except HTTPException as error:
                return error.status_code

    try:
        return asyncio.run(asyncio.wait_for(scenario(), 30))
    finally:
        asyncio.run(engine.dispose())


# The still-admin happy path is test_invites.py (creates an invite; one audit row).
def test_an_actor_demoted_after_the_guard_cannot_create_an_invite():
    actor = add_member("admin")
    assert _create_as_loaded_actor(actor) == 403
    assert invite_ids() == []
    assert audit_rows() == []


def test_a_concurrent_demotion_waits_for_the_actor_lock():
    admin_id = add_member("admin")

    async def scenario():
        engine = create_async_engine(app_db.ASYNC_DB_URL)
        try:
            async with AsyncSession(engine) as holder, AsyncSession(engine) as demoter:
                assert await member_crud.actor_is_active_admin(holder, admin_id)

                async def demote():
                    await demoter.execute(text("UPDATE members SET role = 'member' WHERE id = :i"), {"i": admin_id})
                    await demoter.commit()

                task = asyncio.create_task(demote())
                _, pending = await asyncio.wait({task}, timeout=0.5)
                assert task in pending  # blocked while the holder's FOR SHARE lock stands
                await holder.commit()
                await asyncio.wait_for(task, 5)
        finally:
            await engine.dispose()

    asyncio.run(asyncio.wait_for(scenario(), 30))
