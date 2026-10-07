"""Member management for admins: DELETE /members/{id} (hard delete, audited).

Covers access, the 422/403/404 order, the self and last-admin 409 rules, effects of a delete, the
audit row (which survives the member and rolls back with the delete) and overlapping requests.
"""
import asyncio
import json
import random

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.crud.audit as audit_crud
import app.crud.member as member_crud
import app.router.member as member_router
from app.auth.auth import create_access_token
from app.db import db as app_db
from app.model.model import Member, MemberAuditEvent
from app.router.member import delete_member, update_member
from app.schema.member import MemberAdminUpdate
from tests.conftest import (
    DEFAULT_PASSWORD,
    active_admins,
    add_member,
    audit_rows,
    member_row,
    signup_member,
    sql,
    two_admins,
)


def _session(engine):
    return AsyncSession(engine, expire_on_commit=False, autoflush=False)


def _call_delete(actor_id, target_id):
    """delete_member called directly with an actor loaded from the database now, to model an actor
    deactivated or demoted after the guard passed. Returns (status, target row, audit rows)."""
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario():
        async with _session(engine) as session:
            actor = await session.get(Member, actor_id)
            try:
                await delete_member(target_id, actor, session)
                return 204
            except HTTPException as error:
                return error.status_code

    try:
        status = asyncio.run(asyncio.wait_for(scenario(), 30))
    finally:
        asyncio.run(engine.dispose())
    return status, member_row(target_id), audit_rows()


# --- who may delete ----------------------------------------------------------

def test_no_token_is_401(client):
    assert client.delete("/members/1").status_code == 401


def test_a_garbage_token_is_401(client, client_with_role):
    client_with_role("admin")  # sets SECRET_KEY
    client.headers["Authorization"] = "Bearer not.a.jwt"
    assert client.delete("/members/1").status_code == 401


@pytest.mark.parametrize("role", ["member", "editor"])
def test_member_and_editor_are_403_and_delete_nothing(client_with_role, role):
    caller = client_with_role(role)
    target = add_member()
    assert caller.delete(f"/members/{target}").status_code == 403
    assert member_row(target) is not None
    assert audit_rows() == []


def test_admin_is_204_with_an_empty_body(client_with_role):
    admin = client_with_role("admin")
    response = admin.delete(f"/members/{add_member()}")
    assert (response.status_code, response.content) == (204, b"")


def test_a_refused_caller_with_an_invalid_or_huge_id_is_401_or_403_not_422_or_404(client, client_with_role):
    assert client.delete("/members/0").status_code == 401
    assert client.delete("/members/99999999999999999999").status_code == 401
    member = client_with_role("member")
    assert member.delete("/members/0").status_code == 403
    assert member.delete("/members/99999999999999999999").status_code == 403
    assert member.delete("/members/99999").status_code == 403


# --- 422, 404 ----------------------------------------------------------------

@pytest.mark.parametrize("path_id", ["0", "-1", "99999999999999999999", "2147483648", "abc"])
def test_an_id_outside_the_integer_range_is_422_not_a_500(client_with_role, path_id):
    assert client_with_role("admin").delete(f"/members/{path_id}").status_code == 422


def test_an_unknown_id_is_404_with_no_audit_row(client_with_role):
    assert client_with_role("admin").delete("/members/99999").status_code == 404
    assert audit_rows() == []


def test_a_stale_actor_gets_403_not_404_for_a_missing_id():
    actor = add_member("admin", is_active=False)
    assert _call_delete(actor, 99999)[0] == 403


# --- effects -----------------------------------------------------------------

def test_a_delete_removes_the_row_the_token_the_login_and_the_listing(client, client_with_role):
    admin = client_with_role("admin")
    assert signup_member(client, email="gone@example.com").status_code == 201
    login = client.post("/members/login", json={"email": "gone@example.com", "password": DEFAULT_PASSWORD})
    token = login.json()["access_token"]
    target = sql("SELECT id FROM members WHERE email = 'gone@example.com'").scalar_one()
    assert client.get("/members/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert target in [m["id"] for m in admin.get("/members").json()]

    assert admin.delete(f"/members/{target}").status_code == 204

    assert member_row(target) is None
    assert client.get("/members/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    gone = client.post("/members/login", json={"email": "gone@example.com", "password": DEFAULT_PASSWORD})
    wrong = client.post("/members/login", json={"email": "gone@example.com", "password": "nope-nope-nope"})
    assert (gone.status_code, gone.json(), dict(gone.headers)) == (401, wrong.json(), dict(wrong.headers))
    assert target not in [m["id"] for m in admin.get("/members").json()]


def test_a_second_delete_is_404_and_the_id_is_not_reused(client_with_role):
    admin = client_with_role("admin")
    target = add_member()
    assert admin.delete(f"/members/{target}").status_code == 204
    assert admin.delete(f"/members/{target}").status_code == 404
    assert len(audit_rows()) == 1
    # the identity sequence is not reset (only the test database restarts identities between tests).
    assert add_member() > target


# --- the audit row -----------------------------------------------------------

def test_the_audit_row_has_the_actor_the_target_and_only_the_role(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor", display_name="Eve Editor")
    email = member_row(target).email
    assert admin.delete(f"/members/{target}").status_code == 204
    rows = audit_rows()
    assert len(rows) == 1
    actor_id, action, target_id, detail = rows[0]
    assert (actor_id, action, target_id) == (admin.member_id, "delete", target)
    assert detail == {"role": "editor"}
    everything = json.dumps([actor_id, action, target_id, detail])
    assert email not in everything and "Eve" not in everything and "example.com" not in everything


def test_the_audit_row_survives_the_delete_and_stays_readable(client_with_role):
    admin = client_with_role("admin")
    target = add_member("member")
    admin.delete(f"/members/{target}")
    assert member_row(target) is None
    assert audit_rows() == [(admin.member_id, "delete", target, {"role": "member"})]
    # The actor going away later does not touch it either (no foreign keys).
    sql("DELETE FROM members WHERE id = :id", id=admin.member_id)
    assert len(audit_rows()) == 1


@pytest.mark.parametrize("role", ["member", "editor", "admin"])
def test_deleting_each_role_is_allowed_and_audited_with_that_role(client_with_role, role):
    admin = client_with_role("admin")
    other_admin_kept = add_member("admin")  # the target may be an admin too
    target = add_member(role)
    assert admin.delete(f"/members/{target}").status_code == 204
    assert member_row(target) is None and member_row(other_admin_kept) is not None
    assert audit_rows() == [(admin.member_id, "delete", target, {"role": role})]


def test_an_inactive_admin_can_be_deleted_when_the_actor_is_the_only_active_admin(client_with_role):
    admin = client_with_role("admin")
    inactive = add_member("admin", is_active=False)
    assert admin.delete(f"/members/{inactive}").status_code == 204
    assert audit_rows() == [(admin.member_id, "delete", inactive, {"role": "admin"})]
    assert active_admins() == 1


def test_a_non_sole_active_admin_can_be_deleted(client_with_role):
    admin = client_with_role("admin")
    other = add_member("admin")
    assert admin.delete(f"/members/{other}").status_code == 204
    assert active_admins() == 1


# --- 409 and the stale actor -------------------------------------------------

@pytest.mark.parametrize("another_admin", [False, True])
def test_an_admin_cannot_delete_themselves(client_with_role, another_admin):
    admin = client_with_role("admin")
    if another_admin:
        add_member("admin")
    response = admin.delete(f"/members/{admin.member_id}")
    assert response.status_code == 409
    assert "themselves" in response.json()["detail"]
    assert member_row(admin.member_id) is not None
    assert audit_rows() == []


def test_the_last_admin_decision_refuses_deleting_the_only_active_admin():
    refusal = member_router.last_admin_refusal
    assert "cannot be deleted" in refusal(5, [5], {}, deleting=True)
    # Not the sole active admin, or nobody locked: not refused. Without `deleting`
    # an empty change set is not a removal either.
    assert refusal(5, [5, 6], {}, deleting=True) is None
    assert refusal(7, [5], {}, deleting=True) is None
    assert refusal(5, [], {}, deleting=True) is None
    assert refusal(5, [5], {}) is None


def test_over_http_the_sole_admin_is_refused_by_the_self_rule_first(client_with_role):
    admin = client_with_role("admin")
    response = admin.delete(f"/members/{admin.member_id}")
    assert response.status_code == 409 and "themselves" in response.json()["detail"]
    assert active_admins() == 1


def test_an_actor_deactivated_after_authenticating_is_refused_403_and_deletes_nothing():
    actor = add_member("admin")
    editor = add_member("editor")
    sql("UPDATE members SET is_active = false WHERE id = :id", id=actor)  # after the guard
    status, row, audit = _call_delete(actor, editor)
    assert (status, row.role, audit) == (403, "editor", [])


def test_a_demoted_actor_gets_403_not_404_for_a_missing_id():
    actor = add_member("admin")
    sql("UPDATE members SET role = 'editor' WHERE id = :id", id=actor)  # after the guard
    status, _, audit = _call_delete(actor, 99999)
    assert (status, audit) == (403, [])
    assert member_row(actor).role == "editor"


def test_the_handler_asks_the_last_admin_rule_with_the_locked_admin_ids_and_obeys_it(monkeypatch):
    # Over HTTP the rule is unreachable (the self rule answers first), so pin the
    # wiring: force a refusal and check what the handler passed and what it did.
    actor = add_member("admin")
    target = add_member("editor")
    calls = []

    def forced(*args, **kwargs):
        calls.append((args, kwargs))
        return "forced"

    monkeypatch.setattr(member_router, "last_admin_refusal", forced)
    status, row, audit = _call_delete(actor, target)
    assert (status, row.role, audit) == (409, "editor", [])
    assert calls == [((target, [actor], {}), {"deleting": True})]


def test_an_actor_demoted_after_authenticating_is_refused_403_and_deletes_nothing():
    actor = add_member("admin")
    plain = add_member("member")
    sql("UPDATE members SET role = 'editor' WHERE id = :id", id=actor)  # after the guard
    status, row, audit = _call_delete(actor, plain)
    assert (status, row.role, audit) == (403, "member", [])


def test_a_stale_actor_who_is_also_the_target_is_refused_403_not_409():
    actor = add_member("admin", is_active=False)
    status, row, audit = _call_delete(actor, actor)
    assert (status, row is not None, audit) == (403, True, [])


def test_the_target_being_returned_does_not_make_the_actor_an_active_admin():
    actor = add_member("admin", is_active=False)
    other_admin = add_member("admin")
    status, row, audit = _call_delete(actor, other_admin)
    assert (status, row.role, audit) == (403, "admin", [])


def test_a_healthy_actor_through_the_handler_deletes_and_audits():
    actor = add_member("admin")
    editor = add_member("editor")
    status, row, audit = _call_delete(actor, editor)
    assert (status, row, audit) == (204, None, [(actor, "delete", editor, {"role": "editor"})])


def test_the_order_of_checks_is_422_then_403_then_404_then_409(client_with_role):
    admin = client_with_role("admin")
    assert admin.delete("/members/0").status_code == 422
    assert admin.delete("/members/99999").status_code == 404
    assert admin.delete(f"/members/{admin.member_id}").status_code == 409


def test_a_refused_request_changes_nothing_and_writes_no_audit_row(client_with_role):
    admin = client_with_role("admin")
    plain_caller = client_with_role("member")
    target = add_member("editor")
    before = (member_row(admin.member_id), member_row(target))
    for response in (
        plain_caller.delete(f"/members/{target}"),
        admin.delete("/members/0"),
        admin.delete("/members/99999"),
        admin.delete(f"/members/{admin.member_id}"),
    ):
        assert response.status_code in (403, 422, 404, 409)
    assert (member_row(admin.member_id), member_row(target)) == before
    assert audit_rows() == []


# --- one transaction with the audit row --------------------------------------

def test_if_the_audit_insert_fails_the_request_fails_and_the_member_is_not_deleted(client_with_role, monkeypatch):
    admin = client_with_role("admin")
    target = add_member("editor")

    async def broken(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(audit_crud, "record_member_delete", broken)
    assert admin.delete(f"/members/{target}").status_code == 500
    assert member_row(target).role == "editor"
    assert audit_rows() == []


def test_if_the_database_rejects_the_audit_row_the_member_is_not_deleted(client_with_role, monkeypatch):
    # A real database failure, no DDL: no action, which the NOT NULL column refuses
    # at the commit that would also have made the delete permanent.
    admin = client_with_role("admin")
    target = add_member("editor")

    async def bad_row(db, actor_id, target_id, role):
        db.add(MemberAuditEvent(actor_id=actor_id, action=None, target_id=target_id, detail={"role": role}))

    monkeypatch.setattr(audit_crud, "record_member_delete", bad_row)
    assert admin.delete(f"/members/{target}").status_code == 500
    assert member_row(target).role == "editor"
    assert audit_rows() == []


# --- overlapping requests ----------------------------------------------------

def test_two_admins_deleting_each_other_through_the_handler_leave_one_admin(monkeypatch):
    """The first request is held after its audit helper (delete flushed, locks held); the second
    blocks, then finds its actor gone: a stale actor, 403. One admin left, one audit row."""
    a, b = two_admins()
    engine = create_async_engine(app_db.ASYNC_DB_URL)
    real_record = audit_crud.record_member_delete

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()

        async def held(*args, **kwargs):
            await real_record(*args, **kwargs)
            reached.set()
            await release.wait()

        async def request(actor_id, target_id, session):
            actor = await session.get(Member, actor_id)
            try:
                await delete_member(target_id, actor, session)
                return 204
            except HTTPException as error:
                return error.status_code

        async with _session(engine) as s1, _session(engine) as s2:
            monkeypatch.setattr(audit_crud, "record_member_delete", held)
            first = asyncio.create_task(request(a, b, s1))
            await asyncio.wait_for(reached.wait(), 10)  # a regression fails, it does not hang
            monkeypatch.setattr(audit_crud, "record_member_delete", real_record)
            second = asyncio.create_task(request(b, a, s2))
            await asyncio.sleep(1.0)
            assert not second.done(), "the second request should be waiting on the locks"
            release.set()
            return await asyncio.wait_for(first, 10), await asyncio.wait_for(second, 10)

    try:
        results = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())
    assert results == (204, 403)
    assert active_admins() == 1
    assert member_row(a) is not None and member_row(b) is None
    assert audit_rows() == [(a, "delete", b, {"role": "admin"})]


def test_a_delete_and_a_concurrent_patch_of_the_same_member_are_serialised(monkeypatch):
    """Admin A deletes a member (held after the audit helper) while admin B patches
    that member: B waits on the lock, then finds no member (404). No deadlock, no 500."""
    a, b = two_admins()
    target = add_member("member")
    engine = create_async_engine(app_db.ASYNC_DB_URL)
    real_record = audit_crud.record_member_delete

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()

        async def held(*args, **kwargs):
            await real_record(*args, **kwargs)
            reached.set()
            await release.wait()

        async def run(call, session):
            try:
                await call(session)
                return 200
            except HTTPException as error:
                return error.status_code

        async def do_delete(session):
            await delete_member(target, await session.get(Member, a), session)

        async def do_patch(session):
            await update_member(target, MemberAdminUpdate(role="editor"), await session.get(Member, b), session)

        async with _session(engine) as s1, _session(engine) as s2:
            monkeypatch.setattr(audit_crud, "record_member_delete", held)
            first = asyncio.create_task(run(do_delete, s1))
            await asyncio.wait_for(reached.wait(), 10)
            second = asyncio.create_task(run(do_patch, s2))
            await asyncio.sleep(1.0)
            assert not second.done(), "the patch should be waiting on the member row lock"
            release.set()
            return await asyncio.wait_for(first, 10), await asyncio.wait_for(second, 10)

    try:
        results = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())
    assert results == (200, 404)
    assert member_row(target) is None
    assert audit_rows() == [(a, "delete", target, {"role": "member"})]


def test_many_concurrent_deletes_and_patches_through_the_handlers_never_deadlock():
    """8 workers send random deletes and patches among 4 admins (never deleted) and 8 members straight
    to the handlers. Refusals are fine; any other error (a deadlock victim) is not. The last active
    admin must survive and at least one delete and one patch must succeed, so refusals cannot pass."""
    ids = [add_member("admin") for _ in range(4)] + [add_member("member") for _ in range(8)]
    engine = create_async_engine(app_db.ASYNC_DB_URL, pool_size=8)

    async def worker(seed):
        rng, problems, done = random.Random(seed), [], {"delete": 0, "patch": 0}
        for _ in range(12):
            async with _session(engine) as session:
                try:
                    actor = await session.get(Member, rng.choice(ids[:4]))
                    kind = rng.choice(["delete", "role", "active", "budget"])
                    if kind == "delete":
                        await delete_member(rng.choice(ids[4:]), actor, session)
                        done["delete"] += 1
                    else:
                        payload = {
                            "role": {"role": rng.choice(["admin", "editor", "member"])},
                            "active": {"is_active": rng.choice([True, False])},
                            "budget": {"tokens_budget_daily": rng.randrange(1000)},
                        }[kind]
                        await update_member(rng.choice(ids), MemberAdminUpdate(**payload), actor, session)
                        done["patch"] += 1
                except HTTPException:
                    pass
                except Exception as error:
                    problems.append(type(error).__name__)
        return problems, done

    async def scenario():
        return await asyncio.wait_for(asyncio.gather(*(worker(seed) for seed in range(8))), 60)

    try:
        results = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())
    assert [p for problems, _ in results for p in problems] == []
    assert sum(done["delete"] for _, done in results) >= 1
    assert sum(done["patch"] for _, done in results) >= 1
    assert active_admins() >= 1
