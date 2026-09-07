from sqlalchemy import create_engine, text

# db.py owns the connection settings and the single Base. Import them; never
# redefine Base here, or create_all() would act on an empty second registry.
from app.db.db import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME, Base

# Importing the model module is what registers its classes on Base.metadata.
# Without this, create_all() succeeds and creates nothing. One import per file.
import app.model.model  # noqa: F401
from app.model.model import (
    UNCATEGORISED_MAIN_CATEGORY_ID,
    UNCATEGORISED_SUB_CATEGORY_ID,
)

# Synchronous counterpart of db.py's async URL, from the same psycopg 3 package.
DB_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DB_URL, echo=True)

# --- the "Uncategorised" bucket -------------------------------------------
#
# Deleting a sub-category refiles its documents here instead of destroying
# them. The id has THREE homes and they must agree: the Python constants in
# model.py, the seed below, and the literal inside the trigger body -- PL/pgSQL
# cannot read a Python constant, so the trigger's copy is interpolated here, at
# migration time, and cannot drift from the seed it sits next to. CI asserts the
# seeded ids equal the constants.
#
# Explicit ids do NOT advance a SERIAL sequence, so both sequences are setval'd
# afterwards. Without that the next INSERT collides on the primary key --
# UniqueViolation on main_categories_pkey, which a naive 409 handler would
# report as "a category with that slug already exists".
SEED_BUCKET_SQL = f"""
INSERT INTO main_categories (id, slug, name, position)
     VALUES ({UNCATEGORISED_MAIN_CATEGORY_ID}, 'uncategorised', 'Uncategorised', 0);
INSERT INTO sub_categories (id, main_category_id, slug, name, position)
     VALUES ({UNCATEGORISED_SUB_CATEGORY_ID}, {UNCATEGORISED_MAIN_CATEGORY_ID},
             'uncategorised', 'Uncategorised', 0);
SELECT setval('main_categories_id_seq', (SELECT max(id) FROM main_categories));
SELECT setval('sub_categories_id_seq',  (SELECT max(id) FROM sub_categories));
"""

# CREATE OR REPLACE: drop_all() drops the tables (and with them the trigger),
# but a FUNCTION is not a table and survives every rebuild.
#
# BEFORE DELETE, so the UPDATE lands before the FK's RESTRICT is evaluated. It
# is in the schema rather than in the router because raw SQL and psql route
# around Python entirely -- and because the empty bucket would otherwise be
# deletable, after which every later sub-category delete fails with an error
# naming `items`, a table the caller never touched.
#
# RAISE EXCEPTION arrives at psycopg as RaiseException, sqlstate P0001;
# app/router/category.py maps exactly that one sqlstate to HTTP 409.
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
    # drop_all() destroys every mapped table and its rows. Fine while the schema
    # is still changing; never run it against real content.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text(SEED_BUCKET_SQL))
        connection.execute(text(CREATE_REFILE_TRIGGER_SQL))


if __name__ == "__main__":
    reset_database()
