"""Roles: who may write to the CMS (members.role, require_editor / require_admin).

Content writes (add and delete) are for editors and admins. A bearer token alone
is not enough: anyone holding an invite can sign up, so "has a token" only means
"signed up".
"""
import importlib
import pkgutil

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth.dependency import require_admin, require_editor, require_roles
from app.db.migrate_db import engine as sync_engine
from app.main import app
from app.model import model
from app.model.model import UNCATEGORISED_MAIN_CATEGORY_ID, UNCATEGORISED_SUB_CATEGORY_ID
from tests.conftest import signup_member

PASSWORD = "correct horse battery"

# Every route that mutates the CMS, in the order the matrix runs them. If a new
# write route appears this list, the route-guard test below, and the OpenAPI
# scan in test_auth.py must all be updated on purpose.
WRITE_ROUTES = [
    "POST /main-categories",
    "POST /sub-categories",
    "POST /items",
    "DELETE /sub-categories/{sub_category_id}",
    "DELETE /main-categories/{main_category_id}",
]
EDITOR_ROLES = ("editor", "admin")
# The routes only an admin may call (member management).
ADMIN_ROUTES = [
    "GET /members",
    "GET /members/invites",
    "DELETE /members/invites/{invite_id}",
    "POST /members/invites",
    "PATCH /members/{member_id}",
    "DELETE /members/{member_id}",
]
ADMIN_ROLES = ("admin",)


def sql(statement, **params):
    with sync_engine.begin() as connection:
        return connection.execute(text(statement), params)


def _guard_roles(route: APIRoute) -> set[tuple[str, ...]]:
    """The allowed-role tuples of every require_roles() guard on a route,
    found by walking its dependency tree (route-level and parameter-level)."""
    found: set[tuple[str, ...]] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        roles = getattr(dependant.call, "allowed_roles", None)
        if roles is not None:
            found.add(roles)
        stack.extend(dependant.dependencies)
    return found


# --- the route guards themselves ---------------------------------------------

def _all_route_guards() -> dict[str, set[tuple[str, ...]]]:
    """"METHOD /path" -> role guards, for every route of every module in app/router.

    Walks each router's own `.routes` (public API) rather than `app.routes`:
    this FastAPI wraps an included router in a lazy private object, so the app
    does not list its routes directly. The OpenAPI cross-check below proves no
    route was missed."""
    guarded = {}
    for module in pkgutil.iter_modules(importlib.import_module("app.router").__path__):
        router = importlib.import_module(f"app.router.{module.name}").router
        for route in router.routes:
            assert isinstance(route, APIRoute), route
            roles = _guard_roles(route)
            for method in route.methods - {"HEAD", "OPTIONS"}:
                guarded[f"{method} {route.path}"] = roles
    return guarded


def test_exactly_the_five_cms_write_routes_are_guarded_by_require_editor_and_the_member_routes_by_require_admin():
    """Fails if a guard is removed from one of the five routes or from GET
    /members, PATCH /members/{member_id} or DELETE /members/{member_id}, or added to (or missing on) any other route."""
    guarded = _all_route_guards()

    # Cross-check: this saw every route the app serves (bar the root "/").
    served = {
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert set(guarded) == served - {"GET /"}

    assert {key for key, roles in guarded.items() if roles == {EDITOR_ROLES}} == set(WRITE_ROUTES)
    assert {key for key, roles in guarded.items() if roles == {ADMIN_ROLES}} == set(ADMIN_ROUTES)
    # And nothing else carries any role guard at all.
    assert {key for key, roles in guarded.items() if roles} == set(WRITE_ROUTES) | set(ADMIN_ROUTES)


def test_require_editor_and_require_admin_admit_who_the_brief_says():
    assert require_editor.allowed_roles == ("editor", "admin")
    assert require_admin.allowed_roles == ("admin",)


def test_require_roles_refuses_a_role_that_does_not_exist():
    with pytest.raises(ValueError):
        require_roles("editor", "superuser")


# --- role-by-route matrix ----------------------------------------------------

def _run_write_routes(client) -> list[int]:
    """Call the five write routes in dependency order and return their statuses.
    Meant for a caller allowed to write: each id needed by a later call comes
    from an earlier response."""
    main = client.post("/main-categories", json={"slug": "m", "name": "M"})
    sub = client.post(
        "/sub-categories",
        json={"main_category_id": main.json().get("id", 0), "slug": "s", "name": "S"},
    )
    with sync_engine.begin() as connection:
        crop_id = connection.execute(
            text(
                "INSERT INTO crops (slug, common_name, scientific_name) "
                "VALUES ('basil', 'basil', 'Ocimum basilicum') "
                "ON CONFLICT (slug) DO UPDATE SET slug = 'basil' RETURNING id"
            )
        ).scalar_one()
    item = client.post(
        "/items",
        json={
            "sub_category_id": UNCATEGORISED_SUB_CATEGORY_ID,
            "crop_id": crop_id,
            "title": "t",
            "body": "b",
            "source": "s",
            "reference": "r",
            "url": "u",
            "read_directly": True,
        },
    )
    del_sub = client.delete(f"/sub-categories/{sub.json().get('id', 0)}")
    del_main = client.delete(f"/main-categories/{main.json().get('id', 0)}")
    return [r.status_code for r in (main, sub, item, del_sub, del_main)]


def _requests_for_member_without_setup(client) -> list[int]:
    """The five calls with throwaway ids: a guard must answer before the route
    looks anything up, so a refused caller sees 403 whether or not the row exists."""
    return [
        client.post("/main-categories", json={"slug": "m", "name": "M"}).status_code,
        client.post(
            "/sub-categories", json={"main_category_id": 1, "slug": "s", "name": "S"}
        ).status_code,
        client.post(
            "/items",
            json={
                "sub_category_id": 1, "crop_id": 1, "title": "t", "body": "b",
                "source": "s", "reference": "r", "url": "u", "read_directly": True,
            },
        ).status_code,
        client.delete("/sub-categories/2").status_code,
        client.delete("/main-categories/2").status_code,
    ]


def test_no_token_is_401_on_all_five_write_routes(client):
    assert _requests_for_member_without_setup(client) == [401] * 5


def test_an_invalid_body_without_a_token_is_still_401_not_422(client):
    assert client.post("/main-categories", json={}).status_code == 401


def test_a_garbage_token_is_401_on_all_five_write_routes(client, client_with_role):
    client_with_role("editor")  # sets SECRET_KEY
    client.headers["Authorization"] = "Bearer not.a.jwt"
    assert _requests_for_member_without_setup(client) == [401] * 5


def test_member_is_403_on_all_five_write_routes(client_with_role):
    member = client_with_role("member")
    assert _requests_for_member_without_setup(member) == [403] * 5


def test_a_member_with_an_invalid_body_is_403_not_422(client_with_role):
    assert client_with_role("member").post("/main-categories", json={}).status_code == 403


def _seed_rows() -> dict[str, int]:
    """A real crop, main category and sub-category, ids taken from RETURNING."""
    with sync_engine.begin() as connection:
        crop = connection.execute(text(
            "INSERT INTO crops (slug, common_name, scientific_name) "
            "VALUES ('seeded-crop', 'seeded', 'Seedus cropus') RETURNING id"
        )).scalar_one()
        main = connection.execute(text(
            "INSERT INTO main_categories (slug, name) VALUES ('seeded-main', 'Seeded') RETURNING id"
        )).scalar_one()
        sub = connection.execute(
            text(
                "INSERT INTO sub_categories (main_category_id, slug, name) "
                "VALUES (:main, 'seeded-sub', 'Seeded') RETURNING id"
            ),
            {"main": main},
        ).scalar_one()
    return {"crop": crop, "main": main, "sub": sub}


def _calls_that_would_change_rows(client, ids) -> list[int]:
    """The five write calls with REAL ids and distinct slugs/titles, ordered so
    nothing cancels: the POSTs go first (an unguarded one really inserts), then
    the DELETEs hit the seeded rows. Used for a caller who must be refused."""
    return [
        client.post("/main-categories", json={"slug": "new-main", "name": "New"}).status_code,
        client.post(
            "/sub-categories",
            json={"main_category_id": ids["main"], "slug": "new-sub", "name": "New"},
        ).status_code,
        client.post(
            "/items",
            json={
                "sub_category_id": ids["sub"], "crop_id": ids["crop"], "title": "new-item",
                "body": "b", "source": "s", "reference": "r", "url": "u", "read_directly": True,
            },
        ).status_code,
        client.delete(f"/sub-categories/{ids['sub']}").status_code,
        client.delete(f"/main-categories/{ids['main']}").status_code,
    ]


def test_a_refused_member_changes_nothing(client_with_role):
    ids = _seed_rows()
    member = client_with_role("member")
    before = {
        table: sql(f"SELECT id FROM {table} ORDER BY id").scalars().all()
        for table in ("main_categories", "sub_categories", "items", "crops")
    }
    assert before["main_categories"] == [UNCATEGORISED_MAIN_CATEGORY_ID, ids["main"]]
    assert before["sub_categories"] == [UNCATEGORISED_SUB_CATEGORY_ID, ids["sub"]]

    statuses = _calls_that_would_change_rows(member, ids)

    # Rows first: this test's claim is about the data, not the status codes.
    for table, rows in before.items():
        assert sql(f"SELECT id FROM {table} ORDER BY id").scalars().all() == rows, table
    assert sql("SELECT count(*) FROM items").scalar_one() == 0
    assert statuses == [403] * 5


@pytest.mark.parametrize("role", EDITOR_ROLES)
def test_editor_and_admin_clear_all_five_write_routes(client_with_role, role):
    # 201 x3 for the creates, 200 for the sub-category delete (it returns a
    # count), 204 for the main-category delete.
    assert _run_write_routes(client_with_role(role)) == [201, 201, 201, 200, 204]


@pytest.mark.parametrize("path", ["/", "/main-categories", "/sub-categories", "/crops", "/items"])
def test_reads_stay_open_to_every_role(client, client_with_role, path):
    assert client.get(path).status_code == 200
    assert client_with_role("member").get(path).status_code == 200


# --- where a role comes from -------------------------------------------------

def test_signup_cannot_set_a_role(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    response = signup_member(client, email="sneaky@example.com", password=PASSWORD, role="admin")
    assert response.status_code == 201
    assert sql("SELECT role FROM members WHERE email = 'sneaky@example.com'").scalar_one() == "member"


def test_a_new_member_defaults_to_member(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    signup_member(client, email="plain@example.com", password=PASSWORD)
    token = client.post(
        "/members/login", json={"email": "plain@example.com", "password": PASSWORD}
    ).json()["access_token"]
    refused = client.post(
        "/main-categories",
        json={"slug": "x", "name": "X"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert refused.status_code == 403


@pytest.mark.parametrize("bad", ["superuser", "Admin", "", "editor "])
def test_the_database_check_rejects_an_unknown_role(client_with_role, bad):
    member = client_with_role("member")
    with pytest.raises(IntegrityError) as excinfo:
        sql("UPDATE members SET role = :role WHERE id = :id", role=bad, id=member.member_id)
    assert getattr(excinfo.value.orig, "sqlstate", None) == "23514"
    assert sql("SELECT role FROM members WHERE id = :id", id=member.member_id).scalar_one() == "member"


def test_the_database_check_rejects_an_unknown_role_on_insert(client):
    with pytest.raises(IntegrityError):
        sql("INSERT INTO members (email, password_hash, role) VALUES ('a@b.co', 'x', 'superuser')")


def test_role_is_read_from_the_member_row_not_the_token(client_with_role):
    """Demote an editor: the token they already hold stops working at once;
    promote them back and the same token works again. Nothing about a role is
    in the token, so nothing waits for it to expire."""
    editor = client_with_role("editor")
    body = {"slug": "first", "name": "First"}
    assert editor.post("/main-categories", json=body).status_code == 201

    sql("UPDATE members SET role = 'member' WHERE id = :id", id=editor.member_id)
    assert editor.post("/main-categories", json={"slug": "second", "name": "Second"}).status_code == 403

    sql("UPDATE members SET role = 'admin' WHERE id = :id", id=editor.member_id)
    assert editor.post("/main-categories", json={"slug": "third", "name": "Third"}).status_code == 201


def test_the_token_carries_no_role_claim(client, monkeypatch):
    """Decode the token the real POST /members/login returns, not a minted one."""
    import jwt

    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    signup_member(client, email="claims@example.com", password=PASSWORD)
    sql("UPDATE members SET role = 'admin' WHERE email = 'claims@example.com'")
    response = client.post(
        "/members/login", json={"email": "claims@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    claims = jwt.decode(response.json()["access_token"], options={"verify_signature": False})
    assert set(claims) == {"sub", "iat", "exp"}


def test_signup_promote_login_then_write_and_a_promotion_reaches_an_issued_token(client, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    email = "promoted@example.com"
    assert signup_member(client, email=email, password=PASSWORD).status_code == 201
    old_token = client.post(
        "/members/login", json={"email": email, "password": PASSWORD}
    ).json()["access_token"]
    old = {"Authorization": f"Bearer {old_token}"}
    # Issued while role=member: refused.
    assert client.post(
        "/main-categories", json={"slug": "a", "name": "A"}, headers=old
    ).status_code == 403

    sql("UPDATE members SET role = 'editor' WHERE email = :email", email=email)

    # The SAME token now works, and so does a freshly issued one.
    assert client.post(
        "/main-categories", json={"slug": "b", "name": "B"}, headers=old
    ).status_code == 201
    new_token = client.post(
        "/members/login", json={"email": email, "password": PASSWORD}
    ).json()["access_token"]
    assert client.post(
        "/main-categories",
        json={"slug": "c", "name": "C"},
        headers={"Authorization": f"Bearer {new_token}"},
    ).status_code == 201


def test_member_roles_match_what_the_database_check_accepts(client_with_role):
    """MEMBER_ROLES, the model CHECK and the migration CHECK are three copies of
    one list; `alembic check` does not compare CHECKs, so pin the pair that
    matters here against the live constraint by trying each candidate."""
    member = client_with_role("member")
    candidates = set(model.MEMBER_ROLES) | {"superuser", "root", "owner", "moderator", "Admin", ""}
    accepted = set()
    for role in candidates:
        with sync_engine.connect() as connection:
            transaction = connection.begin()
            try:
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE members SET role = :role WHERE id = :id"),
                        {"role": role, "id": member.member_id},
                    )
                accepted.add(role)
            except IntegrityError as error:
                assert getattr(error.orig, "sqlstate", None) == "23514"
            finally:
                transaction.rollback()
    assert accepted == set(model.MEMBER_ROLES)
    assert sql("SELECT role FROM members WHERE id = :id", id=member.member_id).scalar_one() == "member"
