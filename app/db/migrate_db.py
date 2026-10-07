from sqlalchemy import create_engine, text

# db.py owns the connection settings and the single Base; never redefine it here, or
# create_all() would act on an empty second registry.
from app.db.db import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME, Base

# imported twice: this bare import documents the registration side effect (puts the models on
# Base.metadata), the `from` import below supplies the id constants.
import app.model.model  # noqa: F401
from app.model.model import (
    UNCATEGORISED_MAIN_CATEGORY_ID,
    UNCATEGORISED_SUB_CATEGORY_ID,
)

# synchronous counterpart of db.py's async URL (same psycopg 3 package).
DB_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DB_URL, echo=True)

# --- the "Uncategorised" bucket ---
# refile target of a deleted sub-category (id duplicated in the trigger, see model.py). An explicit
# id does not advance the id counter, hence setval; else the next INSERT collides.
SEED_BUCKET_SQL = f"""
INSERT INTO main_categories (id, slug, name, position)
     VALUES ({UNCATEGORISED_MAIN_CATEGORY_ID}, 'uncategorised', 'Uncategorised', 0);
INSERT INTO sub_categories (id, main_category_id, slug, name, position)
     VALUES ({UNCATEGORISED_SUB_CATEGORY_ID}, {UNCATEGORISED_MAIN_CATEGORY_ID},
             'uncategorised', 'Uncategorised', 0);
SELECT setval('main_categories_id_seq', (SELECT max(id) FROM main_categories));
SELECT setval('sub_categories_id_seq',  (SELECT max(id) FROM sub_categories));
"""

# CREATE OR REPLACE: drop_all() removes the tables and their triggers but not functions.
# BEFORE DELETE so documents move before RESTRICT is checked; in the database because psql and
# raw SQL bypass Python. The RAISE arrives as P0001, which category.py turns into a 409.
CREATE_REFILE_TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION refile_items_to_uncategorised() RETURNS trigger AS $$
BEGIN
    IF OLD.id = {UNCATEGORISED_SUB_CATEGORY_ID} THEN
        RAISE EXCEPTION
            'the "Uncategorised" sub-category cannot be deleted: it is where documents from deleted sub-categories are refiled to';
    END IF;
    UPDATE items
       SET sub_category_id = {UNCATEGORISED_SUB_CATEGORY_ID}
     WHERE sub_category_id = OLD.id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER refile_items_before_sub_category_delete
    BEFORE DELETE ON sub_categories
    FOR EACH ROW EXECUTE FUNCTION refile_items_to_uncategorised();
"""


def reset_database():
    # with an `alembic_version` table Alembic owns the schema: drop_all() would leave that table and
    # rebuild a schema behind a version row still claiming head, silently. Refuse, not warn.
    # Only tests/test_migrate_db_guard.py calls this. DuplicateTable = pre-Alembic DB: README "Migrations".
    with engine.begin() as connection:
        managed = connection.execute(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        ).scalar()
    if managed:
        raise RuntimeError(
            "refusing to run: this database has an 'alembic_version' table, "
            "so Alembic manages its schema now. Use `alembic upgrade head` "
            "instead of migrate_db.py (see the README's Migrations section)."
        )

    # drop_all() destroys every mapped table and its rows; never run it against real content.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text(SEED_BUCKET_SQL))
        connection.execute(text(CREATE_REFILE_TRIGGER_SQL))


if __name__ == "__main__":
    raise SystemExit("retired: use `alembic upgrade head` (see README, Migrations)")
