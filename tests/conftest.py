"""Shared fixtures.

Everything in this suite runs against `db-test`/`cms_test`, never the dev
server's `db`/`cms` -- see test_categories.py::test_suite_talks_to_the_test_database_never_dev
for the guard test proper. This module checks the same thing again,
immediately before _clean_database TRUNCATEs anything: that fixture runs
before every single test, so if it were ever pointed at the dev database it
would not just fail loudly, it would wipe it.
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.migrate_db import SEED_BUCKET_SQL
from app.db.migrate_db import engine as sync_engine
from app.main import app

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
    test_categories.py::test_bucket_survives_truncate_and_reseed -- which
    exercises this exact function a second time within one test to
    demonstrate precondition #2 of plan-2's "Preconditions on the sibling
    plan": a per-test TRUNCATE that does not reseed the bucket (main_category
    id 1 / sub_category id 1) breaks every test after the first one that
    touches it.

    Uses migrate_db.py's own sync engine and SEED_BUCKET_SQL rather than
    duplicating the seed here, so the two cannot drift apart.
    """
    with sync_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE items, sub_categories, main_categories, crops "
                "RESTART IDENTITY CASCADE"
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
