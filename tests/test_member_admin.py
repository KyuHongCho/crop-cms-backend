"""Member management for admins: GET /members (list, page, filter).

The role matrix uses client_with_role (an authorised client, no argon2). The one
test that signs in for real uses signup_member and /members/login.
"""
import secrets

import pytest
from sqlalchemy import text

from app.db.migrate_db import engine as sync_engine
from app.model.model import MEMBER_ROLES
from app.schema.member import MemberAdminView
from tests.conftest import DEFAULT_PASSWORD, signup_member


def sql(statement, **params):
    with sync_engine.begin() as connection:
        return connection.execute(text(statement), params)


def add_members(count, role="member"):
    """`count` members by SQL, ids ascending in call order; returns their ids."""
    return [
        sql(
            "INSERT INTO members (email, password_hash, role) "
            "VALUES (:email, 'not-a-real-hash', :role) RETURNING id",
            email=f"{role}-{i}-{secrets.token_hex(3)}@example.com", role=role,
        ).scalar_one()
        for i in range(count)
    ]


# --- who may list ------------------------------------------------------------

def test_no_token_is_401(client):
    assert client.get("/members").status_code == 401


def test_a_garbage_token_is_401(client, client_with_role):
    client_with_role("admin")  # sets SECRET_KEY
    client.headers["Authorization"] = "Bearer not.a.jwt"
    assert client.get("/members").status_code == 401


@pytest.mark.parametrize("role", ["member", "editor"])
def test_member_and_editor_are_403(client_with_role, role):
    assert client_with_role(role).get("/members").status_code == 403


def test_a_refused_caller_gets_403_even_with_an_invalid_query(client_with_role):
    assert client_with_role("member").get("/members?limit=0").status_code == 403


def test_admin_is_200(client_with_role):
    assert client_with_role("admin").get("/members").status_code == 200


# --- what comes back ---------------------------------------------------------

def test_each_item_has_exactly_the_admin_view_fields_and_no_password_hash(client_with_role):
    admin = client_with_role("admin")
    items = admin.get("/members").json()
    assert len(items) == 1
    assert set(items[0]) == set(MemberAdminView.model_fields)
    assert set(items[0]) == {
        "id", "email", "display_name", "role", "is_active",
        "tokens_used_today", "tokens_budget_daily", "budget_window_start",
    }
    assert "password_hash" not in admin.get("/members").text
    assert "not-a-real-hash" not in admin.get("/members").text


def test_a_member_who_signed_up_for_real_is_listed_with_their_defaults(client, client_with_role):
    admin = client_with_role("admin")
    created = signup_member(client, display_name="Kim")
    assert created.status_code == 201
    listed = {item["id"]: item for item in admin.get("/members").json()}
    item = listed[created.json()["id"]]
    assert item["email"] == "grower@example.com"
    assert item["display_name"] == "Kim"
    assert item["role"] == "member"
    assert item["is_active"] is True
    assert item["tokens_used_today"] == 0
    assert item["tokens_budget_daily"] == 20000
    assert "password_hash" not in item


def test_a_promoted_member_logs_in_and_lists_members(client, client_with_role):
    # The same path an operator takes: sign up, promote by SQL, log in.
    client_with_role("member")  # sets SECRET_KEY
    signup_member(client, email="boss@example.com")
    sql("UPDATE members SET role = 'admin' WHERE email = 'boss@example.com'")
    token = client.post(
        "/members/login", json={"email": "boss@example.com", "password": DEFAULT_PASSWORD}
    ).json()["access_token"]
    response = client.get("/members", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "boss@example.com" in {item["email"] for item in response.json()}


# --- limit, offset -----------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    ["limit=101", "limit=0", "limit=-1", "limit=abc", "offset=-1", "role=superuser", "role=Admin", "is_active=maybe"],
)
def test_out_of_range_parameters_are_422(client_with_role, query):
    assert client_with_role("admin").get(f"/members?{query}").status_code == 422


def test_limit_100_is_accepted(client_with_role):
    assert client_with_role("admin").get("/members?limit=100").status_code == 200


def test_the_default_limit_is_50(client_with_role):
    admin = client_with_role("admin")
    add_members(54)  # 55 members with the admin
    assert len(admin.get("/members").json()) == 50
    assert len(admin.get("/members?limit=100").json()) == 55


def test_ordered_by_id_and_paged_by_limit_and_offset(client_with_role):
    admin = client_with_role("admin")
    ids = [admin.member_id, *add_members(6)]  # 7 members, ids ascending
    assert [m["id"] for m in admin.get("/members?limit=100").json()] == ids
    assert [m["id"] for m in admin.get("/members?limit=3").json()] == ids[:3]
    assert [m["id"] for m in admin.get("/members?limit=3&offset=3").json()] == ids[3:6]
    assert [m["id"] for m in admin.get("/members?limit=3&offset=6").json()] == ids[6:]
    assert admin.get("/members?offset=7").json() == []


def test_ordered_by_id_not_by_email(client_with_role):
    admin = client_with_role("admin", email="zzz@example.com")
    sql("INSERT INTO members (email, password_hash) VALUES ('aaa@example.com', 'x')")
    emails = [m["email"] for m in admin.get("/members").json()]
    assert emails == ["zzz@example.com", "aaa@example.com"]


# --- filters -----------------------------------------------------------------

def test_filter_by_role(client_with_role):
    admin = client_with_role("admin")
    editors = add_members(2, role="editor")
    add_members(3, role="member")
    for role, expected in (("editor", editors), ("admin", [admin.member_id])):
        assert [m["id"] for m in admin.get(f"/members?role={role}").json()] == expected
    assert len(admin.get("/members?role=member").json()) == 3
    assert {m["role"] for m in admin.get("/members").json()} == set(MEMBER_ROLES)


def test_filter_by_is_active(client_with_role):
    admin = client_with_role("admin")
    ids = add_members(4)
    sql("UPDATE members SET is_active = false WHERE id = ANY(:ids)", ids=ids[:2])
    inactive = admin.get("/members?is_active=false").json()
    active = admin.get("/members?is_active=true").json()
    assert [m["id"] for m in inactive] == ids[:2]
    assert all(m["is_active"] is False for m in inactive)
    assert [m["id"] for m in active] == [admin.member_id, *ids[2:]]
    assert len(admin.get("/members").json()) == 5


def test_filters_combine_with_paging(client_with_role):
    admin = client_with_role("admin")
    editors = add_members(3, role="editor")
    sql("UPDATE members SET is_active = false WHERE id = :id", id=editors[0])
    page = admin.get("/members?role=editor&is_active=true&limit=1&offset=1").json()
    assert [m["id"] for m in page] == [editors[2]]
