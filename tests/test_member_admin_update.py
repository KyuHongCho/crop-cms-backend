"""Member management for admins: PATCH /members/{id} (role, daily budget, is_active).

Covers who may call it, the 422 and 404 cases, the self and last-admin 409
rules, one audit row per real change in the same transaction as the change, and
the two overlapping-transaction tests (plain count versus FOR UPDATE). The role
matrix uses client_with_role; the one test that needs a real login uses
signup_member.
"""
import asyncio
import random
import secrets
import threading

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.crud.audit as audit_crud
import app.crud.member as member_crud
from app.auth.budget import require_budget
from app.db import db as app_db
from app.db.db import get_db
from app.db.migrate_db import engine as sync_engine
from app.model.model import Member, MemberAuditEvent
import app.router.member as member_router
from app.router.member import update_member
from app.schema.member import MemberAdminUpdate, MemberAdminView
from tests.conftest import (
    DEFAULT_PASSWORD,
    active_admins,
    add_member,
    audit_rows,
    call_handler,
    member_row,
    signup_member,
    sql,
    two_admins,
)


# --- who may patch -----------------------------------------------------------

def test_no_token_is_401(client):
    assert client.patch("/members/1", json={"role": "editor"}).status_code == 401


def test_a_garbage_token_is_401(client, client_with_role):
    client_with_role("admin")  # sets SECRET_KEY
    client.headers["Authorization"] = "Bearer not.a.jwt"
    assert client.patch("/members/1", json={"role": "editor"}).status_code == 401


@pytest.mark.parametrize("role", ["member", "editor"])
def test_member_and_editor_are_403_and_change_nothing(client_with_role, role):
    caller = client_with_role(role)
    target = add_member()
    assert caller.patch(f"/members/{target}", json={"role": "admin"}).status_code == 403
    assert member_row(target).role == "member"
    assert audit_rows() == []


def test_admin_is_200(client_with_role):
    admin = client_with_role("admin")
    target = add_member()
    assert admin.patch(f"/members/{target}", json={"role": "editor"}).status_code == 200


def test_a_refused_caller_with_an_invalid_body_is_401_or_403_not_422(client, client_with_role):
    assert client.patch("/members/1", json={}).status_code == 401
    assert client.patch("/members/1", json={"role": "superuser"}).status_code == 401
    member = client_with_role("member")
    assert member.patch("/members/1", json={}).status_code == 403
    assert member.patch("/members/1", json={"role": "superuser", "x": 1}).status_code == 403


def test_a_refused_caller_gets_403_not_404_for_a_missing_id(client_with_role):
    assert client_with_role("editor").patch("/members/99999", json={"role": "admin"}).status_code == 403


# --- 422 and 404 -------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        {"is_active": "true"},
        {"is_active": "false"},
        {"is_active": 1},
        {"is_active": 0},
        {"is_active": None},
        {"is_active": [True]},
        {"tokens_used_today": 0},
        {"password_hash": "x"},
        {"email": "new@example.com"},
        {"id": 1},
        {"role": "editor", "tokens_used_today": 0},
        {},
        {"role": "superuser"},
        {"role": "Admin"},
        {"role": None},
        {"tokens_budget_daily": -1},
        {"tokens_budget_daily": 10_000_001},
        {"tokens_budget_daily": None},
        {"tokens_budget_daily": "lots"},
    ],
)
def test_invalid_bodies_are_422_and_change_nothing(client_with_role, body):
    admin = client_with_role("admin")
    target = add_member()
    before = member_row(target)
    assert admin.patch(f"/members/{target}", json=body).status_code == 422
    assert member_row(target) == before
    assert audit_rows() == []


@pytest.mark.parametrize("path_id", ["0", "-1", "99999999999999999999", "2147483648"])
def test_an_id_outside_the_integer_range_is_422_not_a_500(client_with_role, path_id):
    assert client_with_role("admin").patch(f"/members/{path_id}", json={"role": "editor"}).status_code == 422


def test_a_refused_caller_with_a_huge_id_is_403(client_with_role):
    assert client_with_role("member").patch("/members/99999999999999999999", json={"role": "editor"}).status_code == 403


@pytest.mark.parametrize("budget", [True, False, "5", " 7 ", 5.0, 1e3, 5.5, [1]])
def test_a_budget_must_be_a_json_integer_nothing_is_coerced(client_with_role, budget):
    admin = client_with_role("admin")
    target = add_member(tokens_budget_daily=20000)
    assert admin.patch(f"/members/{target}", json={"tokens_budget_daily": budget}).status_code == 422
    assert member_row(target).tokens_budget_daily == 20000
    assert audit_rows() == []


def test_a_non_json_body_and_a_non_numeric_id_are_422(client_with_role):
    admin = client_with_role("admin")
    assert admin.patch("/members/1", content="not json", headers={"content-type": "application/json"}).status_code == 422
    assert admin.patch("/members/abc", json={"role": "editor"}).status_code == 422


@pytest.mark.parametrize("budget", [0, 10_000_000])
def test_the_budget_bounds_are_inclusive(client_with_role, budget):
    admin = client_with_role("admin")
    target = add_member()
    response = admin.patch(f"/members/{target}", json={"tokens_budget_daily": budget})
    assert response.status_code == 200
    assert member_row(target).tokens_budget_daily == budget


def test_an_unknown_id_is_404_with_no_audit_row(client_with_role):
    admin = client_with_role("admin")
    assert admin.patch("/members/99999", json={"role": "editor"}).status_code == 404
    assert audit_rows() == []


def test_validation_comes_before_404(client_with_role):
    assert client_with_role("admin").patch("/members/99999", json={}).status_code == 422


# --- what changes, what comes back -------------------------------------------

def test_only_the_sent_fields_change(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor", tokens_budget_daily=777, tokens_used_today=5, display_name="Kim")
    before = member_row(target)

    assert admin.patch(f"/members/{target}", json={"tokens_budget_daily": 900}).status_code == 200
    after = member_row(target)
    assert after.tokens_budget_daily == 900
    assert (after.role, after.is_active, after.tokens_used_today, after.email, after.password_hash) == (
        before.role, before.is_active, before.tokens_used_today, before.email, before.password_hash,
    )

    assert admin.patch(f"/members/{target}", json={"role": "member"}).status_code == 200
    after = member_row(target)
    assert (after.role, after.tokens_budget_daily) == ("member", 900)


def test_the_response_is_the_admin_view_of_the_updated_member_without_the_hash(client_with_role):
    admin = client_with_role("admin")
    target = add_member(tokens_budget_daily=100)
    response = admin.patch(f"/members/{target}", json={"role": "editor", "tokens_budget_daily": 5000})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(MemberAdminView.model_fields)
    assert body["id"] == target and body["role"] == "editor" and body["tokens_budget_daily"] == 5000
    assert body["is_active"] is True
    assert "password_hash" not in response.text and "not-a-real-hash" not in response.text


def test_raising_the_budget_lets_an_exhausted_member_through_require_budget_again(client, client_with_role):
    admin = client_with_role("admin")  # also sets SECRET_KEY
    created = signup_member(client)
    member_id = created.json()["id"]
    token = client.post(
        "/members/login", json={"email": "grower@example.com", "password": DEFAULT_PASSWORD}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    sql("UPDATE members SET tokens_used_today = 20000 WHERE id = :id", id=member_id)

    probe = FastAPI()

    @probe.post("/fake-chat")
    async def fake_chat(member=Depends(require_budget)):
        return {"ok": True}

    probe_client = TestClient(probe, raise_server_exceptions=False)
    assert probe_client.post("/fake-chat", headers=headers).status_code == 429

    assert admin.patch(f"/members/{member_id}", json={"tokens_budget_daily": 50000}).status_code == 200
    assert probe_client.post("/fake-chat", headers=headers).status_code == 200


# --- the self and last-admin rules (409, no audit row) -----------------------

@pytest.mark.parametrize("new_role", ["member", "editor"])
def test_an_admin_cannot_change_their_own_role(client_with_role, new_role):
    admin = client_with_role("admin")
    other_admin = add_member("admin")  # so this is not the last-admin rule
    response = admin.patch(f"/members/{admin.member_id}", json={"role": new_role})
    assert response.status_code == 409
    assert "own role" in response.json()["detail"]
    assert member_row(admin.member_id).role == "admin"
    assert member_row(other_admin).role == "admin"
    assert audit_rows() == []


def test_an_admin_may_change_their_own_budget_and_re_send_their_own_role(client_with_role):
    admin = client_with_role("admin")
    response = admin.patch(f"/members/{admin.member_id}", json={"role": "admin", "tokens_budget_daily": 1234})
    assert response.status_code == 200
    assert member_row(admin.member_id).tokens_budget_daily == 1234
    assert member_row(admin.member_id).role == "admin"
    assert [r.detail for r in audit_rows()] == [{"tokens_budget_daily": {"from": 20000, "to": 1234}}]


def test_over_http_two_admins_demote_one_at_a_time_and_the_survivor_is_protected_by_the_self_rule(client_with_role):
    # Over HTTP the actor is itself an active admin (an inactive one is refused at
    # the door), so the target is never the ONLY active admin: the rule is
    # unreachable here and the two admins demote each other fine, one at a time.
    actor = client_with_role("admin")
    other = add_member("admin")
    assert actor.patch(f"/members/{other}", json={"role": "editor"}).status_code == 200
    # Now the actor is the sole active admin: it cannot demote itself (self rule).
    assert actor.patch(f"/members/{actor.member_id}", json={"role": "editor"}).status_code == 409


def test_a_healthy_active_admin_still_works_through_the_handler():
    actor = add_member("admin")
    editor = add_member("editor")
    assert call_handler(actor, editor, role="member")[0] == 200
    assert call_handler(actor, actor, tokens_budget_daily=7)[0] == 200  # own budget
    status, row, audit = call_handler(actor, actor, role="admin", is_active=True)  # re-send
    assert (status, row.is_active) == (200, True)
    assert len(audit) == 2


def test_an_actor_deactivated_after_authenticating_is_refused_403_and_nothing_changes():
    actor = add_member("admin")
    editor = add_member("editor")
    sql("UPDATE members SET is_active = false WHERE id = :id", id=actor)  # after the guard
    for payload in ({"is_active": False}, {"role": "member"}, {"tokens_budget_daily": 5}):
        status, row, audit = call_handler(actor, editor, **payload)
        assert status == 403, payload
        assert (row.role, row.is_active, row.tokens_budget_daily) == ("editor", True, 20000)
        assert audit == []


def test_an_actor_demoted_after_authenticating_cannot_promote_anyone():
    actor = add_member("admin")
    plain = add_member("member")
    sql("UPDATE members SET role = 'editor' WHERE id = :id", id=actor)  # after the guard
    status, row, audit = call_handler(actor, plain, role="admin")
    assert (status, row.role, audit) == (403, "member", [])


def test_a_stale_actor_gets_403_before_404_so_it_learns_nothing_about_ids():
    actor = add_member("admin", is_active=False)
    assert call_handler(actor, 99999, role="editor")[0] == 403


def test_a_stale_actor_who_is_also_the_target_is_refused_403():
    actor = add_member("admin", is_active=False)
    status, row, audit = call_handler(actor, actor, is_active=True)  # would re-activate itself
    assert (status, row.is_active, audit) == (403, False, [])


def test_the_target_being_returned_does_not_make_the_actor_an_active_admin():
    # The lock statement returns the target whatever it is; only admin_ids says
    # who is an active admin. A stale actor aimed at an active admin is refused.
    actor = add_member("admin", is_active=False)
    other_admin = add_member("admin")
    status, row, audit = call_handler(actor, other_admin, role="member")
    assert (status, row.role, audit) == (403, "admin", [])


def test_the_last_admin_decision_refuses_removing_or_deactivating_the_only_active_admin():
    # The decision on the locked rows. Over HTTP the actor is in the locked set, so
    # the target is the only active admin only when actor == target (the self rule
    # answers first); this pins the rule itself, built from the real lock result.
    sole = add_member("admin")
    other = add_member("member")
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def locked(member_id):
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            _, admin_ids = await member_crud.lock_member_and_active_admins(session, member_id)
            await session.rollback()
            return admin_ids

    try:
        ids = asyncio.run(locked(sole))
        other_ids = asyncio.run(locked(other))
    finally:
        asyncio.run(engine.dispose())
    assert ids == [sole] == other_ids
    refusal = member_router.last_admin_refusal
    assert "admin role" in refusal(sole, ids, {"role": {"from": "admin", "to": "editor"}})
    assert "deactivated" in refusal(sole, ids, {"is_active": {"from": True, "to": False}})
    assert "admin role" in refusal(sole, ids, {"role": {"from": "admin", "to": "member"}, "is_active": {"from": True, "to": False}})
    # Not removals: budget, reactivation, a target who is not the sole active admin.
    assert refusal(sole, ids, {"tokens_budget_daily": {"from": 1, "to": 2}}) is None
    assert refusal(sole, ids, {"is_active": {"from": False, "to": True}}) is None
    assert refusal(other, ids, {"role": {"from": "member", "to": "editor"}}) is None
    assert refusal(sole, [sole, 99], {"role": {"from": "admin", "to": "editor"}}) is None
    assert refusal(sole, [], {"role": {"from": "admin", "to": "editor"}}) is None


def test_the_lock_returns_only_the_active_admins_and_the_target():
    inactive_admin = add_member("admin", is_active=False)
    active_admin = add_member("admin")
    editor = add_member("editor")
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def locked(member_id):
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            target, admin_ids = await member_crud.lock_member_and_active_admins(session, member_id)
            found = target.id, admin_ids
            await session.rollback()  # release the locks
            return found

    try:
        assert asyncio.run(locked(inactive_admin)) == (inactive_admin, [active_admin])
        assert asyncio.run(locked(editor)) == (editor, [active_admin])
        sql("UPDATE members SET is_active = false WHERE id = :id", id=active_admin)
        # No active admin at all: an inactive admin target is NOT counted as one.
        assert asyncio.run(locked(inactive_admin)) == (inactive_admin, [])
    finally:
        asyncio.run(engine.dispose())


def test_an_inactive_admin_is_not_counted_as_the_last_active_admin():
    """An inactive admin target is not "the only active admin" (the decision gets []),
    and an inactive actor is refused 403. The `row.is_active` mutant is killed by
    test_the_lock_returns_only_the_active_admins_and_the_target and by
    test_a_stale_actor_who_is_also_the_target_is_refused_403."""
    actor = add_member("admin", is_active=False)
    target = add_member("admin", is_active=False)
    status, row, audit = call_handler(actor, target, role="member")
    assert (status, row.role, audit) == (403, "admin", [])
    assert member_router.last_admin_refusal(target, [], {"role": {"from": "admin", "to": "member"}}) is None


def test_one_of_two_active_admins_can_be_demoted_by_the_other(client_with_role):
    actor = client_with_role("admin")
    other = add_member("admin")
    assert actor.patch(f"/members/{other}", json={"role": "member"}).status_code == 200
    assert member_row(other).role == "member"


def test_an_inactive_admin_can_be_demoted_while_another_admin_is_the_only_active_one(client_with_role):
    actor = client_with_role("admin")
    inactive = add_member("admin", is_active=False)
    assert actor.patch(f"/members/{inactive}", json={"role": "member"}).status_code == 200


def test_the_sole_admin_demoting_themselves_is_refused_as_self_demotion(client_with_role):
    admin = client_with_role("admin")
    response = admin.patch(f"/members/{admin.member_id}", json={"role": "member"})
    assert response.status_code == 409
    assert "own role" in response.json()["detail"]
    assert audit_rows() == []


def test_the_order_of_checks_is_422_then_404_then_409(client_with_role):
    admin = client_with_role("admin")
    assert admin.patch(f"/members/{admin.member_id}", json={"role": "superuser"}).status_code == 422
    assert admin.patch("/members/99999", json={"role": "member"}).status_code == 404
    assert admin.patch(f"/members/{admin.member_id}", json={"role": "member"}).status_code == 409


# --- the audit row -----------------------------------------------------------

def test_a_success_writes_one_audit_row_with_only_the_changed_fields(client_with_role):
    admin = client_with_role("admin")
    target = add_member("member", tokens_budget_daily=20000, email="private-person@example.com")
    response = admin.patch(
        f"/members/{target}", json={"role": "editor", "tokens_budget_daily": 50000}
    )
    assert response.status_code == 200
    rows = audit_rows()
    assert len(rows) == 1
    assert rows[0].actor_id == admin.member_id
    assert rows[0].action == "update"
    assert rows[0].target_id == target
    assert rows[0].detail == {
        "role": {"from": "member", "to": "editor"},
        "tokens_budget_daily": {"from": 20000, "to": 50000},
    }
    assert "private-person" not in str(rows[0])
    assert sql("SELECT occurred_at IS NOT NULL FROM member_audit_events").scalar_one()


def test_an_unchanged_field_is_left_out_of_the_detail(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor", tokens_budget_daily=300)
    admin.patch(f"/members/{target}", json={"role": "editor", "tokens_budget_daily": 400})
    assert [r.detail for r in audit_rows()] == [{"tokens_budget_daily": {"from": 300, "to": 400}}]


def test_a_patch_that_changes_nothing_is_200_and_writes_no_audit_row(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor", tokens_budget_daily=300)
    before = member_row(target)
    response = admin.patch(f"/members/{target}", json={"role": "editor", "tokens_budget_daily": 300})
    assert response.status_code == 200
    assert response.json()["role"] == "editor"
    assert member_row(target) == before
    assert audit_rows() == []


def test_each_success_writes_its_own_row(client_with_role):
    admin = client_with_role("admin")
    target = add_member()
    admin.patch(f"/members/{target}", json={"role": "editor"})
    admin.patch(f"/members/{target}", json={"role": "member"})
    assert [r.detail["role"] for r in audit_rows()] == [
        {"from": "member", "to": "editor"}, {"from": "editor", "to": "member"},
    ]


def test_if_the_audit_insert_fails_the_request_fails_and_the_change_is_rolled_back(client_with_role, monkeypatch):
    admin = client_with_role("admin")
    target = add_member("member", tokens_budget_daily=20000)

    async def broken(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(audit_crud, "record_member_update", broken)
    response = admin.patch(f"/members/{target}", json={"role": "editor", "tokens_budget_daily": 1})
    assert response.status_code == 500
    row = member_row(target)
    assert (row.role, row.tokens_budget_daily) == ("member", 20000)
    assert audit_rows() == []


def test_if_the_database_rejects_the_audit_row_the_change_is_rolled_back(client_with_role, monkeypatch):
    # A real database failure, no stub of the failure itself and no DDL: the row
    # has no action, which the NOT NULL column refuses when the commit flushes it.
    admin = client_with_role("admin")
    target = add_member()

    async def bad_row(db, actor_id, target_id, changes):
        db.add(MemberAuditEvent(actor_id=actor_id, action=None, target_id=target_id, detail=changes))

    monkeypatch.setattr(audit_crud, "record_member_update", bad_row)
    response = admin.patch(f"/members/{target}", json={"role": "editor"})
    assert response.status_code == 500
    assert member_row(target).role == "member"
    assert audit_rows() == []


# --- overlapping transactions ------------------------------------------------

def test_two_admins_demoting_each_other_through_the_handler_leave_one_admin(monkeypatch):
    """The same overlap through the real update_member: the first request is
    held after its audit helper runs (change flushed, lock held, not committed);
    the second must block on the lock, then find its actor no longer an active
    admin and answer 403 (the actor check comes before the last-admin rule)."""
    a, b = two_admins()
    engine = create_async_engine(app_db.ASYNC_DB_URL)
    real_record = audit_crud.record_member_update

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()

        async def held(*args, **kwargs):
            await real_record(*args, **kwargs)
            reached.set()
            await release.wait()

        async def request(actor_id, target_id, session):
            actor = await session.get(Member, actor_id)
            try:
                await update_member(target_id, MemberAdminUpdate(role="member"), actor, session)
                return 200
            except Exception as error:  # HTTPException
                return error.status_code

        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as s1, \
                AsyncSession(engine, expire_on_commit=False, autoflush=False) as s2:
            monkeypatch.setattr(audit_crud, "record_member_update", held)
            first = asyncio.create_task(request(a, b, s1))
            await asyncio.wait_for(reached.wait(), 10)  # a regression fails, it does not hang
            monkeypatch.setattr(audit_crud, "record_member_update", real_record)
            second = asyncio.create_task(request(b, a, s2))
            await asyncio.sleep(1.0)
            assert not second.done(), "the second request should be waiting on the admin row locks"
            release.set()
            return await asyncio.wait_for(first, 10), await asyncio.wait_for(second, 10)

    try:
        results = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())
    assert results == (200, 403)
    assert active_admins() == 1
    assert len(audit_rows()) == 1


def test_many_concurrent_patches_through_the_handler_never_deadlock():
    """10 workers send random role and budget changes (promotions and demotions
    among 4 admins and 4 members) straight to update_member on their own
    sessions. A refusal (409) is fine; a database error (a deadlock victim
    would surface as one, and a 500) is not. At least one patch must have
    gone through (200), so a run drained by refusals cannot pass."""
    ids = [add_member("admin") for _ in range(4)] + [add_member("member") for _ in range(4)]
    engine = create_async_engine(app_db.ASYNC_DB_URL, pool_size=10)

    async def worker(seed):
        rng, problems, done = random.Random(seed), [], 0
        for _ in range(40):
            payload = rng.choice([
                {"role": rng.choice(["admin", "editor", "member"])},
                {"tokens_budget_daily": rng.randrange(1000)},
            ])
            async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
                try:
                    actor = await session.get(Member, rng.choice(ids[:4]))
                    await update_member(rng.choice(ids), MemberAdminUpdate(**payload), actor, session)
                    done += 1
                except HTTPException:
                    pass
                except Exception as error:
                    problems.append(type(error).__name__)
        return problems, done

    async def scenario():
        return await asyncio.wait_for(asyncio.gather(*(worker(seed) for seed in range(10))), 120)

    try:
        results = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())
    assert [p for problems, _ in results for p in problems] == []
    assert sum(done for _, done in results) >= 1
    assert active_admins() >= 1


def test_why_the_lock_is_one_statement_two_statement_locking_deadlocks():
    """A "why" test: it documents the Postgres hazard the single ordered statement
    avoids and exercises no app code (raw SQL on the sync engine, two connections,
    the two queries in the order the first design ran them). t1 locks the admin set
    {a}; N is promoted and committed; t2 locks the new set {N, a}, waits on a;
    t1 then locks N."""
    n = add_member("member")  # the lower id
    a = add_member("admin")
    admins = "SELECT id FROM members WHERE role = 'admin' AND is_active ORDER BY id FOR UPDATE"
    outcome = {}

    def t2_request(connection):
        try:
            with connection.begin():
                connection.execute(text(admins)).all()
            outcome["t2"] = "ok"
        except Exception as error:
            outcome["t2"] = type(error.orig).__name__

    with sync_engine.connect() as t1, sync_engine.connect() as t2:
        t1_tx = t1.begin()
        assert t1.execute(text(admins)).scalars().all() == [a]
        sql("UPDATE members SET role = 'admin' WHERE id = :id", id=n)
        thread = threading.Thread(target=t2_request, args=(t2,), daemon=True)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive()  # t2 holds n and waits on a
        try:
            t1.execute(text("SELECT id FROM members WHERE id = :id FOR UPDATE"), {"id": n}).all()
            outcome["t1"] = "ok"
        except Exception as error:
            outcome["t1"] = type(error.orig).__name__
        t1_tx.rollback()
        thread.join(timeout=10)
    assert "DeadlockDetected" in outcome.values(), outcome


def test_the_lock_statement_takes_the_locks_in_ascending_id_order():
    """A holder keeps the highest-id admin locked. The request for the lowest
    id's member must already hold every lower row while it waits for that one:
    a descending order would hold nothing yet."""
    low_member = add_member("member")
    low_admin = add_member("admin")
    high_admin = add_member("admin")
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario(release_holder):
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            task = asyncio.create_task(
                member_crud.lock_member_and_active_admins(session, low_member)
            )
            await asyncio.sleep(1.0)
            assert not task.done(), "should be waiting on the held admin row"
            with sync_engine.connect() as probe:
                with probe.begin():
                    held = []
                    for row_id in (low_member, low_admin):
                        try:
                            with probe.begin_nested():
                                probe.execute(
                                    text("SELECT id FROM members WHERE id = :id FOR UPDATE NOWAIT"),
                                    {"id": row_id},
                                )
                        except Exception:
                            held.append(row_id)
            release_holder()
            await asyncio.wait_for(task, 10)
            return held

    with sync_engine.connect() as holder:
        holder_tx = holder.begin()
        holder.execute(text("SELECT id FROM members WHERE id = :id FOR UPDATE"), {"id": high_admin}).all()
        try:
            held = asyncio.run(scenario(holder_tx.rollback))
        finally:
            asyncio.run(engine.dispose())
    assert held == [low_member, low_admin]


def test_the_locked_load_returns_the_current_row_not_an_object_loaded_earlier():
    a = add_member("admin")
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            earlier = await session.get(Member, a)
            sql("UPDATE members SET role = 'editor' WHERE id = :id", id=a)
            got, admin_ids = await member_crud.lock_member_and_active_admins(session, a)
            return earlier, got, admin_ids

    try:
        earlier, got, admin_ids = asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())
    assert got is earlier
    assert got.role == "editor"
    assert admin_ids == []
