"""Shared fixtures.

Everything runs against `db-test`/`cms_test`, never dev `db`/`cms`: this module re-checks that
right before _clean_database TRUNCATEs (it runs before every test and would wipe the dev database).
"""
import asyncio
import os
import secrets

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from app.auth.auth import create_access_token
from app.db import db as app_db
from app.db.migrate_db import SEED_BUCKET_SQL
from app.db.migrate_db import engine as sync_engine
from app.main import app
from app.model.model import Member
from app.router.member import update_member
from app.schema.member import MemberAdminUpdate
from scripts.make_invite import make_invite as _store_invite

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
    """TRUNCATE every content table and put the 'Uncategorised' bucket back (also called mid-test by
    test_categories.py::test_bucket_survives_truncate_and_reseed; skipping the reseed would break every
    later test touching the bucket). Reuses SEED_BUCKET_SQL so the two cannot drift."""
    with sync_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE items, sub_categories, main_categories, crops, members, "
                "member_audit_events, member_invites RESTART IDENTITY CASCADE"
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
    # raise_server_exceptions=False: an unhandled exception surfaces as an HTTP 500, as a real
    # client sees it, not as a Python exception in the test.
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
    """A factory: `client_with_role("editor")` is a TestClient with a valid bearer token for a member
    holding that role. The member is inserted via the sync engine and the token minted directly (no
    argon2 round trip; no role travels through the API). Sets SECRET_KEY itself.
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

    Signup needs an invite, so this stores one unless `invite_code` is in `**extra`; the rest of
    `**extra` joins the JSON body. `invite_role`/`invite_email` are for the helper, never sent."""
    invite_role = extra.pop("invite_role", "member")
    invite_email = extra.pop("invite_email", None)
    if "invite_code" not in extra:
        extra["invite_code"] = make_invite(role=invite_role, email=invite_email)
    return client.post(
        "/members/signup", json={"email": email, "password": password, **extra}
    )


def make_invite(role="member", email=None, days=7):
    """Store one invite by the same code path as scripts/make_invite.py and
    return its plaintext code (what a test hands to signup)."""
    return _store_invite(role=role, days=days, email=email)


# --- member-management helpers shared by the member test modules --------------

def sql(statement, **params):
    with sync_engine.begin() as connection:
        return connection.execute(text(statement), params)


def add_member(role="member", **columns):
    """One member by SQL; returns its id. `columns` are extra column values."""
    columns = {"email": f"{role}-{secrets.token_hex(4)}@example.com", "role": role, **columns}
    names = ", ".join(columns)
    values = ", ".join(f":{name}" for name in columns)
    return sql(
        f"INSERT INTO members (password_hash, {names}) VALUES ('not-a-real-hash', {values}) RETURNING id",
        **columns,
    ).scalar_one()


def member_row(member_id):
    return sql(
        "SELECT role, is_active, tokens_budget_daily, tokens_used_today, email, password_hash "
        "FROM members WHERE id = :id", id=member_id,
    ).one_or_none()


def audit_rows():
    return sql(
        "SELECT actor_id, action, target_id, detail FROM member_audit_events ORDER BY id"
    ).all()


def call_handler(actor_id, target_id, **payload):
    """update_member called directly with an actor loaded from the database now, to
    model a request whose actor was deactivated or demoted after the guard passed
    (the guard is not run here). Returns (status, the target's row, the audit rows)."""
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
            actor = await session.get(Member, actor_id)
            try:
                await update_member(target_id, MemberAdminUpdate(**payload), actor, session)
                return 200
            except HTTPException as error:
                return error.status_code

    try:
        status = asyncio.run(asyncio.wait_for(scenario(), 30))
    finally:
        asyncio.run(engine.dispose())
    return status, member_row(target_id), audit_rows()


def two_admins():
    return add_member("admin"), add_member("admin")


def active_admins():
    return sql("SELECT count(*) FROM members WHERE role = 'admin' AND is_active").scalar_one()
