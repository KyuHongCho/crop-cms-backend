"""Invites, creation side: POST /members/invites and scripts/make_invite.py.

Role matrix with client_with_role. The stored table is read back by SQL: only a
SHA-256 of the code may be there. Claiming an invite at signup is test_signup_invites.py.
"""
import hashlib
import os
import re
import subprocess
import sys

import pytest

from scripts.make_invite import make_invite
from tests.conftest import audit_rows, make_invite as make_invite_helper, sql

URL = "/members/invites"


def invite_rows():
    return sql(
        "SELECT id, code_hash, email, role, created_at, expires_at, used_at "
        "FROM member_invites ORDER BY id"
    ).all()


def sha256(code):
    return hashlib.sha256(code.encode()).hexdigest()


# --- who may create ----------------------------------------------------------

def test_no_token_is_401_and_stores_nothing(client):
    assert client.post(URL, json={}).status_code == 401
    assert invite_rows() == []


@pytest.mark.parametrize("role", ["member", "editor"])
def test_member_and_editor_are_403_and_store_nothing(client_with_role, role):
    assert client_with_role(role).post(URL, json={}).status_code == 403
    assert invite_rows() == []


def test_a_refused_caller_gets_403_even_with_an_invalid_body(client_with_role):
    assert client_with_role("member").post(URL, json={"role": "admin"}).status_code == 403


def test_admin_creates_an_invite_with_defaults(client_with_role):
    response = client_with_role("admin").post(URL, json={})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "code", "role", "email", "expires_at"}
    assert body["role"] == "member"
    assert body["email"] is None
    (row,) = invite_rows()
    assert row.id == body["id"]
    assert row.used_at is None
    # Default validity is 7 days.
    assert sql(
        "SELECT expires_at - created_at BETWEEN interval '6 days 23 hours' "
        "AND interval '7 days 1 hour' FROM member_invites"
    ).scalar_one()


# --- the code and what is stored --------------------------------------------

def test_the_code_is_43_url_safe_characters_and_only_its_sha256_is_stored(client_with_role):
    admin = client_with_role("admin")
    code = admin.post(URL, json={}).json()["code"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
    (row,) = invite_rows()
    assert re.fullmatch(r"[0-9a-f]{64}", row.code_hash)
    assert row.code_hash == sha256(code)
    # The code appears in no column of the row.
    assert code not in " ".join(str(value) for value in row)


def test_two_invites_have_different_codes(client_with_role):
    admin = client_with_role("admin")
    first = admin.post(URL, json={}).json()["code"]
    second = admin.post(URL, json={}).json()["code"]
    assert first != second
    assert len(invite_rows()) == 2


# --- validation --------------------------------------------------------------

def test_role_admin_is_422_and_stores_nothing(client_with_role):
    assert client_with_role("admin").post(URL, json={"role": "admin"}).status_code == 422
    assert invite_rows() == []


def test_role_editor_is_accepted(client_with_role):
    response = client_with_role("admin").post(URL, json={"role": "editor"})
    assert response.status_code == 201
    assert invite_rows()[0].role == "editor"


@pytest.mark.parametrize("days", [0, -1, 31, 1.5, "7", True, None])
def test_expires_in_days_outside_1_to_30_or_not_an_integer_is_422(client_with_role, days):
    response = client_with_role("admin").post(URL, json={"expires_in_days": days})
    assert response.status_code == 422
    assert invite_rows() == []


@pytest.mark.parametrize("days", [1, 30])
def test_expires_in_days_bounds_are_accepted(client_with_role, days):
    assert client_with_role("admin").post(URL, json={"expires_in_days": days}).status_code == 201
    assert sql(
        "SELECT expires_at - created_at BETWEEN make_interval(days => :d) - interval '1 hour' "
        "AND make_interval(days => :d) + interval '1 hour' FROM member_invites", d=days,
    ).scalar_one()


@pytest.mark.parametrize(
    "email", ["no-at-sign", "a@b@c", "a b@c.io", "a\x00b@x.io", "a@b\x00.io", "\x1c@b", "a@\x1c", "\x1c@\x1c", "\x1f@b", "x" * 256 + "@a.io", "", "@"]
)
def test_a_bad_invite_email_is_422(client_with_role, email):
    assert client_with_role("admin").post(URL, json={"email": email}).status_code == 422
    assert invite_rows() == []


def test_an_unknown_field_is_422(client_with_role):
    response = client_with_role("admin").post(URL, json={"used_at": "2030-01-01T00:00:00Z"})
    assert response.status_code == 422


# --- email normalisation -----------------------------------------------------

def test_the_route_stores_the_email_stripped_and_lowercased(client_with_role):
    response = client_with_role("admin").post(URL, json={"email": " Mixed@Example.COM "})
    assert response.status_code == 201
    assert response.json()["email"] == "mixed@example.com"
    assert invite_rows()[0].email == "mixed@example.com"


def test_the_script_stores_the_email_stripped_and_lowercased():
    code = make_invite(email=" Mixed@Example.COM ")
    (row,) = invite_rows()
    assert row.email == "mixed@example.com"
    assert row.code_hash == sha256(code)


# --- the CHECK ---------------------------------------------------------------

def test_the_database_refuses_an_admin_invite_row():
    with pytest.raises(Exception, match="invite_role_valid"):
        sql(
            "INSERT INTO member_invites (code_hash, role, expires_at) "
            "VALUES (:h, 'admin', now() + interval '1 day')", h="0" * 64,
        )
    assert invite_rows() == []


def test_the_database_refuses_a_duplicate_code_hash():
    insert = "INSERT INTO member_invites (code_hash, expires_at) VALUES (:h, now() + interval '1 day')"
    sql(insert, h="1" * 64)
    with pytest.raises(Exception, match="code_hash"):
        sql(insert, h="1" * 64)


# --- audit -------------------------------------------------------------------

def test_creating_an_invite_writes_one_audit_row_with_no_code_and_no_email(client_with_role):
    admin = client_with_role("admin")
    body = admin.post(URL, json={"role": "editor", "email": "Someone@Example.com"}).json()
    ((actor_id, action, target_id, detail),) = audit_rows()
    assert (actor_id, action, target_id) == (admin.member_id, "invite_create", body["id"])
    assert set(detail) == {"role", "expires_at"}
    assert detail["role"] == "editor"
    text = str(audit_rows())
    assert body["code"] not in text
    assert sha256(body["code"]) not in text
    assert "someone@example.com" not in text.lower()


def test_a_refused_or_invalid_request_writes_no_audit_row(client, client_with_role):
    client.post(URL, json={})
    client_with_role("member").post(URL, json={})
    client_with_role("admin").post(URL, json={"role": "admin"})
    assert audit_rows() == []


def test_if_the_audit_insert_fails_no_invite_is_stored(client_with_role, monkeypatch):
    import app.crud.audit as audit_crud

    async def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit_crud, "record_invite_create", boom)
    assert client_with_role("admin").post(URL, json={}).status_code == 500
    assert invite_rows() == []


# --- the script --------------------------------------------------------------

def test_the_script_prints_a_working_code_once():
    # A subprocess, so the real `python -m scripts.make_invite` entry point runs
    # (it inherits DB_HOST/DB_NAME from the test run, i.e. cms_test).
    done = subprocess.run(
        [sys.executable, "-m", "scripts.make_invite", "--role", "editor", "--days", "3"],
        capture_output=True, text=True, env=os.environ.copy(), timeout=60,
    )
    assert done.returncode == 0, done.stderr
    code = done.stdout.strip()  # stdout is the code and nothing else
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
    (row,) = invite_rows()
    assert row.code_hash == sha256(code)
    assert row.role == "editor"
    assert code not in done.stderr
    assert sql(
        "SELECT expires_at - created_at BETWEEN interval '2 days 23 hours' "
        "AND interval '3 days 1 hour' FROM member_invites"
    ).scalar_one()


@pytest.mark.parametrize("kwargs", [{"role": "admin"}, {"days": 0}, {"days": 31}])
def test_the_script_function_refuses_what_the_route_refuses(kwargs):
    with pytest.raises(ValueError):
        make_invite(**kwargs)
    assert invite_rows() == []


@pytest.mark.parametrize(
    "email", ["a", "   ", "", "no-at-sign", "a@b@c", "a b@c.io", "a\x00b@x.io", "\x1c@b", "a@\x1c", "\x1c@\x1c", "\x1f@b", "x" * 256 + "@a.io", "@"]
)
def test_the_script_function_refuses_a_bad_email_like_the_route(email):
    with pytest.raises(ValueError):
        make_invite(email=email)
    assert invite_rows() == []


def test_the_conftest_helper_stores_an_invite_and_returns_its_code():
    code = make_invite_helper(role="editor", email=" Bound@Example.com ")
    (row,) = invite_rows()
    assert row.code_hash == sha256(code)
    assert (row.role, row.email) == ("editor", "bound@example.com")
    assert row.used_at is None
    make_invite_helper()
    assert [r.role for r in invite_rows()] == ["editor", "member"]
