"""Deactivating and reactivating a member (PATCH is_active), effective at once.

An inactive member's token is refused on the next request (get_current_member
reads is_active per request, so every guarded route is covered) and works again
on reactivation; login with the right password gives the same 401 as a wrong one.
An admin cannot deactivate themselves and the last active admin cannot be
deactivated (409, no audit row). Authorised clients come from client_with_role;
the tests that need a real login use signup_member.
"""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.crud.audit as audit_crud
import app.router.member as member_router
from app.db import db as app_db
from app.model.model import Member
from app.router.member import update_member
from app.schema.member import MemberAdminUpdate
from tests.conftest import DEFAULT_PASSWORD, add_member, audit_rows, call_handler, member_row, signup_member, sql

EMAIL = "grower@example.com"


def login(client, password=DEFAULT_PASSWORD, email=EMAIL):
    return client.post("/members/login", json={"email": email, "password": password})


def deactivate(admin, member_id):
    return admin.patch(f"/members/{member_id}", json={"is_active": False})


def reactivate(admin, member_id):
    return admin.patch(f"/members/{member_id}", json={"is_active": True})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def real_member(client, client_with_role):
    """(admin client, the member's id, a real token for the member): the member signed up
    and logged in for real, so a password hash argon2 can verify exists."""
    admin = client_with_role("admin")  # also sets SECRET_KEY
    member_id = signup_member(client).json()["id"]
    token = login(client).json()["access_token"]
    return admin, member_id, token


# --- the token: 401 at once, and works again on reactivation -------------------

def test_a_deactivated_members_token_is_401_and_works_again_on_reactivation(client, real_member):
    admin, member_id, token = real_member
    assert client.get("/members/me", headers=bearer(token)).status_code == 200

    assert deactivate(admin, member_id).status_code == 200
    refused = client.get("/members/me", headers=bearer(token))
    assert refused.status_code == 401
    assert refused.json() == {"detail": "Not authenticated"}
    assert refused.headers["www-authenticate"] == "Bearer"
    # The very same response as a request with no token at all.
    anonymous = client.get("/members/me")
    assert (refused.content, refused.headers["www-authenticate"]) == (anonymous.content, anonymous.headers["www-authenticate"])

    assert reactivate(admin, member_id).status_code == 200
    again = client.get("/members/me", headers=bearer(token))  # the same token, nothing was revoked
    assert again.status_code == 200
    assert again.json()["id"] == member_id


def test_a_deactivated_editor_is_401_on_a_write_route_and_works_again(client_with_role):
    admin = client_with_role("admin")
    editor = client_with_role("editor")
    body = {"slug": "m1", "name": "M1"}
    assert editor.post("/main-categories", json=body).status_code == 201

    assert deactivate(admin, editor.member_id).status_code == 200
    assert editor.post("/main-categories", json={"slug": "m2", "name": "M2"}).status_code == 401
    assert editor.delete("/main-categories/99999").status_code == 401  # the guard, before the 404

    assert reactivate(admin, editor.member_id).status_code == 200
    assert editor.post("/main-categories", json={"slug": "m2", "name": "M2"}).status_code == 201


def test_a_deactivated_admin_is_401_on_an_admin_route_and_works_again(client_with_role):
    first = client_with_role("admin")
    second = client_with_role("admin")
    assert second.get("/members").status_code == 200
    assert deactivate(first, second.member_id).status_code == 200
    assert second.get("/members").status_code == 401
    assert second.patch(f"/members/{first.member_id}", json={"tokens_budget_daily": 1}).status_code == 401
    assert reactivate(first, second.member_id).status_code == 200
    assert second.get("/members").status_code == 200


def test_a_deactivated_members_chat_request_is_401(client, real_member):
    admin, member_id, token = real_member
    deactivate(admin, member_id)
    response = client.post("/chat", json={"question": "How warm should basil be?"}, headers=bearer(token))
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    reactivate(admin, member_id)
    # Past the door again (what the model layer answers is not this test's business).
    assert client.post("/chat", json={"question": "How warm should basil be?"}, headers=bearer(token)).status_code != 401


# --- login -----------------------------------------------------------------------

def _comparable(response):
    # Every header but the clock: the date can tick between two requests.
    return (
        response.status_code,
        response.content,
        sorted((k, v) for k, v in response.headers.items() if k != "date"),
    )


def test_login_with_the_right_password_for_an_inactive_member_is_the_same_401_as_a_wrong_password(
    client, real_member
):
    admin, member_id, token = real_member
    deactivate(admin, member_id)

    inactive = login(client)
    wrong = login(client, password="not the password")
    unknown = login(client, email="nobody@example.com")
    assert inactive.status_code == 401
    assert inactive.json() == {"detail": "Incorrect email or password"}
    assert _comparable(inactive) == _comparable(wrong) == _comparable(unknown)
    assert "access_token" not in inactive.text


def test_login_verifies_the_password_before_it_looks_at_is_active(client, real_member, monkeypatch):
    admin, member_id, token = real_member
    deactivate(admin, member_id)
    calls = []
    real_verify = member_router.verify_password

    async def spy(password, password_hash):
        calls.append(password)
        return await real_verify(password, password_hash)

    monkeypatch.setattr(member_router, "verify_password", spy)
    assert login(client).status_code == 401
    assert calls == [DEFAULT_PASSWORD]  # argon2 ran, once, same as for a wrong password
    calls.clear()
    assert login(client, password="not the password").status_code == 401
    assert calls == ["not the password"]


def test_after_reactivation_login_works_again_and_the_old_token_still_does(client, real_member):
    admin, member_id, token = real_member
    deactivate(admin, member_id)
    assert login(client).status_code == 401
    reactivate(admin, member_id)
    fresh = login(client)
    assert fresh.status_code == 200
    assert client.get("/members/me", headers=bearer(fresh.json()["access_token"])).status_code == 200
    assert client.get("/members/me", headers=bearer(token)).status_code == 200


# --- what an admin sees ----------------------------------------------------------

def test_a_deactivated_member_is_listed_inactive_and_the_filter_finds_them(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor")
    other = add_member("editor")
    deactivate(admin, target)
    listed = {m["id"]: m for m in admin.get("/members").json()}
    assert listed[target]["is_active"] is False and listed[other]["is_active"] is True
    assert [m["id"] for m in admin.get("/members?is_active=false").json()] == [target]
    assert target not in [m["id"] for m in admin.get("/members?is_active=true").json()]
    assert deactivate(admin, target).json()["is_active"] is False


# --- the audit row -----------------------------------------------------------------

def test_deactivating_and_reactivating_each_write_one_audit_row_with_the_changed_field(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor", email="private-person@example.com")
    assert deactivate(admin, target).status_code == 200
    assert reactivate(admin, target).status_code == 200
    rows = audit_rows()
    assert [(r.actor_id, r.action, r.target_id, r.detail) for r in rows] == [
        (admin.member_id, "update", target, {"is_active": {"from": True, "to": False}}),
        (admin.member_id, "update", target, {"is_active": {"from": False, "to": True}}),
    ]
    assert "private-person" not in str(rows)


def test_is_active_sits_beside_the_other_fields_in_one_row(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor", tokens_budget_daily=100)
    response = admin.patch(
        f"/members/{target}", json={"role": "member", "is_active": False, "tokens_budget_daily": 7}
    )
    assert response.status_code == 200
    assert [r.detail for r in audit_rows()] == [{
        "role": {"from": "editor", "to": "member"},
        "is_active": {"from": True, "to": False},
        "tokens_budget_daily": {"from": 100, "to": 7},
    }]


def test_a_patch_that_changes_is_active_to_what_it_already_is_is_200_with_no_audit_row(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor")
    assert reactivate(admin, target).status_code == 200  # already active
    assert audit_rows() == []
    deactivate(admin, target)
    assert len(audit_rows()) == 1
    assert deactivate(admin, target).status_code == 200  # already inactive
    assert len(audit_rows()) == 1
    # Sent alongside a real change, the unchanged is_active stays out of the detail.
    assert admin.patch(f"/members/{target}", json={"is_active": False, "tokens_budget_daily": 9}).status_code == 200
    assert audit_rows()[-1].detail == {"tokens_budget_daily": {"from": 20000, "to": 9}}


# --- who may, and what is a valid body ------------------------------------------

@pytest.mark.parametrize("role", ["member", "editor"])
def test_member_and_editor_cannot_deactivate_anyone(client_with_role, role):
    caller = client_with_role(role)
    target = add_member("editor")
    assert deactivate(caller, target).status_code == 403
    assert member_row(target).is_active is True
    assert audit_rows() == []


def test_no_token_cannot_deactivate(client):
    assert client.patch("/members/1", json={"is_active": False}).status_code == 401


@pytest.mark.parametrize("value", ["true", "false", "False", 1, 0, None, [False], {}])
def test_is_active_must_be_a_json_boolean_nothing_is_coerced(client_with_role, value):
    admin = client_with_role("admin")
    target = add_member("editor")
    assert admin.patch(f"/members/{target}", json={"is_active": value}).status_code == 422
    assert member_row(target).is_active is True
    assert audit_rows() == []


def test_is_active_alone_is_a_valid_body_and_other_fields_beside_it_are_still_checked(client_with_role):
    admin = client_with_role("admin")
    target = add_member("editor")
    assert admin.patch(f"/members/{target}", json={"is_active": False, "tokens_used_today": 0}).status_code == 422
    assert admin.patch(f"/members/{target}", json={}).status_code == 422
    assert member_row(target).is_active is True
    assert admin.patch(f"/members/{target}", json={"is_active": False}).status_code == 200


def test_validation_and_404_come_before_the_self_rule(client_with_role):
    admin = client_with_role("admin")
    assert admin.patch(f"/members/{admin.member_id}", json={"is_active": "no"}).status_code == 422
    assert admin.patch("/members/99999", json={"is_active": False}).status_code == 404
    assert admin.patch(f"/members/{admin.member_id}", json={"is_active": False}).status_code == 409


# --- the rules (409, no audit row) ---------------------------------------------------

@pytest.mark.parametrize("other_admin_exists", [False, True])
def test_an_admin_cannot_deactivate_themselves(client_with_role, other_admin_exists):
    admin = client_with_role("admin")
    other = add_member("admin") if other_admin_exists else None
    response = deactivate(admin, admin.member_id)
    assert response.status_code == 409
    assert "deactivate themselves" in response.json()["detail"]
    assert member_row(admin.member_id).is_active is True
    assert other is None or member_row(other).is_active is True
    assert audit_rows() == []
    assert admin.get("/members/me").status_code == 200  # still in


def test_an_admin_may_re_send_their_own_is_active_true_and_change_their_own_budget(client_with_role):
    admin = client_with_role("admin")
    assert admin.patch(f"/members/{admin.member_id}", json={"is_active": True}).status_code == 200
    assert audit_rows() == []
    response = admin.patch(f"/members/{admin.member_id}", json={"is_active": True, "tokens_budget_daily": 5})
    assert response.status_code == 200
    assert [r.detail for r in audit_rows()] == [{"tokens_budget_daily": {"from": 20000, "to": 5}}]


def test_one_admin_deactivating_the_other_leaves_one_active_admin_who_cannot_remove_themselves(client_with_role):
    first = client_with_role("admin")
    second = client_with_role("admin")
    assert deactivate(first, second.member_id).status_code == 200
    assert deactivate(first, first.member_id).status_code == 409
    assert first.patch(f"/members/{first.member_id}", json={"role": "member"}).status_code == 409
    assert sql("SELECT count(*) FROM members WHERE role = 'admin' AND is_active").scalar_one() == 1
    assert second.get("/members").status_code == 401  # the deactivated one is out


def test_deactivating_a_non_admin_or_a_non_sole_admin_is_allowed(client_with_role):
    admin = client_with_role("admin")
    editor, plain, other_admin = add_member("editor"), add_member("member"), add_member("admin")
    for target in (editor, plain, other_admin):
        assert deactivate(admin, target).status_code == 200
        assert member_row(target).is_active is False


def test_the_sole_admin_cannot_be_deactivated_by_anyone_the_decision_and_the_self_rule():
    # Over HTTP the actor is in the locked set, so the target is the only active
    # admin only when actor == target: the self rule answers (409, nothing written).
    # The last-admin decision itself is pinned in test_member_admin_update.py.
    sole = add_member("admin")
    status, row, audit = call_handler(sole, sole, is_active=False)
    assert (status, row.is_active, audit) == (409, True, [])
    status, row, audit = call_handler(sole, sole, is_active=False, role="member")
    assert (status, row.role, row.is_active, audit) == (409, "admin", True, [])
    assert member_router.last_admin_refusal(
        sole, [sole], {"is_active": {"from": True, "to": False}}
    ) == "The last active admin cannot be deactivated"


def test_deactivating_an_inactive_admin_is_a_no_op_not_a_last_admin_refusal(client_with_role):
    admin = client_with_role("admin")
    inactive = add_member("admin", is_active=False)
    assert deactivate(admin, inactive).status_code == 200
    assert audit_rows() == []


def test_two_admins_deactivating_each_other_through_the_handler_leave_one_active_admin(monkeypatch):
    """The overlap through the real update_member: the first request is held after
    its audit helper runs (change flushed, locks held, not committed); the second
    must wait on the locks, then find its actor deactivated and answer 403."""
    a, b = add_member("admin"), add_member("admin")
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
                await update_member(target_id, MemberAdminUpdate(is_active=False), actor, session)
                return 200
            except HTTPException as error:
                return error.status_code, error.detail

        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as s1, \
                AsyncSession(engine, expire_on_commit=False, autoflush=False) as s2:
            monkeypatch.setattr(audit_crud, "record_member_update", held)
            first = asyncio.create_task(request(a, b, s1))
            await asyncio.wait_for(reached.wait(), 10)
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
    assert results[0] == 200
    assert results[1] == (403, "Not enough permissions")
    assert sql("SELECT count(*) FROM members WHERE role = 'admin' AND is_active").scalar_one() == 1
    assert member_row(a).is_active is True and member_row(b).is_active is False
    assert len(audit_rows()) == 1
