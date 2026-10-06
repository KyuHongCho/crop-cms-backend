"""Shared fixtures.

Everything in this suite runs against `db-test`/`cms_test`, never the dev
server's `db`/`cms` -- see test_categories.py::test_suite_talks_to_the_test_database_never_dev
for the guard test proper. This module checks the same thing again, right
before _clean_database TRUNCATEs anything, since that fixture runs before
every test: if it were ever pointed at the dev database, it would not just
fail loudly, it would wipe it.
"""
import os
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.auth import create_access_token
from app.db.migrate_db import SEED_BUCKET_SQL
from app.db.migrate_db import engine as sync_engine
from app.main import app

DEFAULT_PASSWORD = "correct horse battery"

_REQUIRED_ENV = {"DB_HOST": "db-test", "DB_NAME": "cms_test"}


def _assert_test_database() -> None:
    got = {key: os.environ.get(key) for key in _REQUIRED_ENV}
    assert got == _REQUIRED_ENV, (
        f"refusing to touch a database that is not db-test/cms_test -- got {got}. "
        "Pass `-e DB_HOST=db-test -e DB_NAME=cms_test` to `docker compose exec`, "
        "or this fixture's TRUNCATE would run against the dev database."
    )


def truncate_and_reseed() -> None:
    """TRUNCATE every content table and put the 'Uncategorised' bucket back.

    Reused by `_clean_database` below and, mid-test, by
    test_categories.py::test_bucket_survives_truncate_and_reseed, which
    calls this function a second time within one test. That demonstrates
    why a per-test TRUNCATE must reseed the bucket (main_category id 1 /
    sub_category id 1) -- skipping it would break every later test that
    touches it.

    Uses migrate_db.py's own sync engine and SEED_BUCKET_SQL rather than
    duplicating the seed here, so the two cannot drift apart.
    """
    with sync_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE items, sub_categories, main_categories, crops, members, "
                "member_audit_events RESTART IDENTITY CASCADE"
            )
        )
        connection.execute(text(SEED_BUCKET_SQL))


@pytest.fixture(autouse=True)
def _clean_database():
    _assert_test_database()
    truncate_and_reseed()
    yield


@pytest.fixture
def client():
    # raise_server_exceptions=False: an unhandled exception in the app
    # surfaces as an HTTP 500 response, the way a real client sees it,
    # instead of bubbling up as a Python exception inside the test.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def sync_db_session():
    """A plain synchronous ORM session against db-test/cms_test, for tests
    that read back what a sync-engine script (e.g. scripts/seed.py) wrote --
    same sync_engine migrate_db.py and _clean_database above already use."""
    with Session(sync_engine) as session:
        yield session


@pytest.fixture
def client_with_role(monkeypatch):
    """A factory: `client_with_role("editor")` is a TestClient already carrying a
    valid bearer token for a member who holds that role.

    The member row is inserted through the sync engine and the token minted
    directly, so no signup/argon2 round trip (and no role ever travels through
    the API, which has no way to set one). It sets SECRET_KEY itself, so tests
    that only want an authorised client need not.
    """
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))

    def make(role: str, email: str | None = None) -> TestClient:
        email = email or f"{role}-{secrets.token_hex(4)}@example.com"
        with sync_engine.begin() as connection:
            member_id = connection.execute(
                text(
                    "INSERT INTO members (email, password_hash, role) "
                    "VALUES (:email, 'not-a-real-hash', :role) RETURNING id"
                ),
                {"email": email, "role": role},
            ).scalar_one()
        member = TestClient(app, raise_server_exceptions=False)
        member.headers["Authorization"] = f"Bearer {create_access_token(member_id)}"
        member.member_id = member_id
        return member

    return make


@pytest.fixture
def editor_client(client_with_role):
    return client_with_role("editor")


def signup_member(client, email="grower@example.com", password=DEFAULT_PASSWORD, **extra):
    """Create a member through the signup route and return the Response.

    The one place a test that needs a real login (a password hash argon2 can
    verify) creates its member, so a change to how members come into being is
    one edit here. `**extra` is merged into the JSON body. Tests whose subject
    is signup itself may still post to /members/signup directly.
    """
    return client.post(
        "/members/signup", json={"email": email, "password": password, **extra}
    )
