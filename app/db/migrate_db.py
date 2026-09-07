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
# Where a deleted sub-category's documents are refiled to. Its id lives in three
# places that must agree: the constants in model.py, the seed below, and the
# trigger body -- which cannot read Python, so its copy is filled in here, next
# to the seed, where the two cannot drift apart. CI re-reads the seeded rows and
# checks them against the constants.
#
# Inserting an explicit id does not move the table's id counter along, so both
# are reset with setval afterwards. Skip that and the next INSERT collides with
# the bucket's own id.
SEED_BUCKET_SQL = f"""
INSERT INTO main_categories (id, slug, name, position)
     VALUES ({UNCATEGORISED_MAIN_CATEGORY_ID}, 'uncategorised', 'Uncategorised', 0);
INSERT INTO sub_categories (id, main_category_id, slug, name, position)
     VALUES ({UNCATEGORISED_SUB_CATEGORY_ID}, {UNCATEGORISED_MAIN_CATEGORY_ID},
             'uncategorised', 'Uncategorised', 0);
SELECT setval('main_categories_id_seq', (SELECT max(id) FROM main_categories));
SELECT setval('sub_categories_id_seq',  (SELECT max(id) FROM sub_categories));
"""

# CREATE OR REPLACE because drop_all() removes the tables and the trigger with
# them, but a function is not a table and survives every rebuild.
#
# BEFORE DELETE, so the documents have moved by the time RESTRICT is checked.
# It lives in the database, not the router, because psql and raw SQL bypass
# Python entirely. The RAISE reaches Python as error code P0001, which
# app/router/category.py turns into an HTTP 409.
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
