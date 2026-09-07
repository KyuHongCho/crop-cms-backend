"""HTTP-level tests for the category endpoints, through TestClient.

Seam: FastAPI's TestClient against app.main.app -- HTTP requests and JSON
responses, never SQLAlchemy internals or ORM objects directly.
"""
import os

from sqlalchemy import text

from app.db.migrate_db import engine as sync_engine
from tests.conftest import truncate_and_reseed


def test_suite_talks_to_the_test_database_never_dev():
    """Written first: every other test here TRUNCATEs tables between runs
    (conftest.py's autouse `_clean_database`). If this suite were ever
    pointed at `db`/`cms` it would wipe the dev database, not just fail
    loudly. `docker compose exec -e DB_HOST=db-test -e DB_NAME=cms_test` is
    what makes this true; a bare `docker compose exec` would fail this.
    """
    assert os.environ.get("DB_HOST") == "db-test"
    assert os.environ.get("DB_NAME") == "cms_test"


def test_duplicate_slug_on_main_categories_returns_409(client):
    body = {"slug": "research-literature", "name": "Research literature"}

    first = client.post("/main-categories", json=body)
    assert first.status_code == 201

    second = client.post("/main-categories", json=body)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["constraint_name"] == "main_categories_slug_key"


def test_desynced_sequence_is_not_reported_as_duplicate_slug(client):
    """A pkey collision from a desynced sequence must name the pkey
    constraint, not the slug constraint -- a blanket `IntegrityError -> 409`
    would report this as "a category with that slug already exists" even
    though no slug was ever duplicated. See plan-2's "Preconditions on the
    sibling plan" #1.
    """
    with sync_engine.begin() as connection:
        # is_called=false with value=1 makes the NEXT nextval() return 1
        # itself -- colliding with the existing bucket row's id, a
        # UniqueViolation with nothing to do with any slug. (Sequences start
        # at 1; setval(..., 0, false) is out of range.)
        connection.execute(text("SELECT setval('main_categories_id_seq', 1, false)"))

    response = client.post(
        "/main-categories", json={"slug": "brand-new-slug", "name": "Brand new"}
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["constraint_name"] == "main_categories_pkey"


def test_bucket_survives_truncate_and_reseed(client):
    """Precondition #2 of plan-2's "Preconditions on the sibling plan": the
    per-test TRUNCATE fixture must reseed the "Uncategorised" bucket, or
    every test after the first one that touches it breaks.

    Runs the exact fixture mechanism (truncate_and_reseed) a second time
    mid-test, simulating the boundary between two tests, then asserts the
    bucket -- and only the bucket -- is still there.
    """
    client.post("/main-categories", json={"slug": "temp", "name": "Temp"})

    truncate_and_reseed()

    response = client.get("/sub-categories")
    assert response.status_code == 200
    slugs = {sub_category["slug"] for sub_category in response.json()}
    assert slugs == {"uncategorised"}
