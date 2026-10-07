"""HTTP-level tests for the category endpoints, through TestClient against app.main.app
(never SQLAlchemy internals or ORM objects)."""
import os

from sqlalchemy import text

from app.db.migrate_db import engine as sync_engine
from tests.conftest import truncate_and_reseed


def test_suite_talks_to_the_test_database_never_dev():
    """A safety check: every other test TRUNCATEs tables (conftest's autouse `_clean_database`), so
    pointing at `db`/`cms` would wipe the dev database. The `-e DB_HOST=db-test -e DB_NAME=cms_test`
    exec is what makes this true; a bare `docker compose exec` fails it.
    """
    assert os.environ.get("DB_HOST") == "db-test"
    assert os.environ.get("DB_NAME") == "cms_test"


def test_duplicate_slug_on_main_categories_returns_409(editor_client):
    body = {"slug": "research-literature", "name": "Research literature"}

    first = editor_client.post("/main-categories", json=body)
    assert first.status_code == 201

    second = editor_client.post("/main-categories", json=body)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["constraint_name"] == "main_categories_slug_key"


def test_desynced_sequence_is_not_reported_as_duplicate_slug(editor_client):
    """A pkey collision from a desynced sequence must name the pkey constraint, not the slug one
    (a blanket `IntegrityError -> 409` would report a duplicate slug that never existed).
    """
    with sync_engine.begin() as connection:
        # is_called=false, value=1 makes the next nextval() return 1, colliding with the bucket
        # row's id (sequences start at 1; setval(..., 0, false) is out of range).
        connection.execute(text("SELECT setval('main_categories_id_seq', 1, false)"))

    response = editor_client.post(
        "/main-categories", json={"slug": "brand-new-slug", "name": "Brand new"}
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["constraint_name"] == "main_categories_pkey"


def test_duplicate_slug_on_sub_categories_same_parent_returns_409(editor_client):
    """Mirrors test_duplicate_slug_on_main_categories_returns_409 but through a composite
    constraint (SubCategory.__table_args__): neither path is exercised by the other's test.
    """
    mc = editor_client.post(
        "/main-categories", json={"slug": "research-literature", "name": "RL"}
    ).json()
    body = {"main_category_id": mc["id"], "slug": "temperature", "name": "Temp"}

    first = editor_client.post("/sub-categories", json=body)
    assert first.status_code == 201

    second = editor_client.post("/sub-categories", json=body)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["constraint_name"] == "sub_categories_main_category_id_slug_key"


def test_same_slug_under_different_parents_both_return_201(editor_client):
    """"Unique per parent, not globally" (README API table): the same slug under two main
    categories must succeed. Verified it can fail: a temporary global UNIQUE(slug) broke it.
    """
    mc1 = editor_client.post(
        "/main-categories", json={"slug": "research-literature", "name": "RL"}
    ).json()
    mc2 = editor_client.post(
        "/main-categories", json={"slug": "crop-profile", "name": "Crop profile"}
    ).json()

    r1 = editor_client.post(
        "/sub-categories",
        json={"main_category_id": mc1["id"], "slug": "temperature", "name": "Temp"},
    )
    r2 = editor_client.post(
        "/sub-categories",
        json={"main_category_id": mc2["id"], "slug": "temperature", "name": "Temp"},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201


def test_bucket_survives_truncate_and_reseed(editor_client, client_with_role):
    """The per-test TRUNCATE fixture must reseed the "Uncategorised" bucket, or every later test
    touching it breaks. Runs truncate_and_reseed mid-test, then asserts only the bucket remains.
    """
    main = editor_client.post("/main-categories", json={"slug": "temp", "name": "Temp"})
    editor_client.post(
        "/sub-categories",
        json={"main_category_id": main.json()["id"], "slug": "temp", "name": "Temp"},
    )

    truncate_and_reseed()
    # The reseed deleted editor_client's member row, so its token is dead now.
    reader = client_with_role("editor")

    main_response = reader.get("/main-categories")
    assert main_response.status_code == 200
    main_slugs = {main_category["slug"] for main_category in main_response.json()}
    assert main_slugs == {"uncategorised"}

    response = reader.get("/sub-categories")
    assert response.status_code == 200
    slugs = {sub_category["slug"] for sub_category in response.json()}
    assert slugs == {"uncategorised"}
