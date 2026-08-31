from sqlalchemy import create_engine

# db.py owns the connection settings and the single Base. Import them; never
# redefine Base here, or create_all() would act on an empty second registry.
from app.db.db import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME, Base

# Importing the model module is what registers its classes on Base.metadata.
# Without this, create_all() succeeds and creates nothing. One import per file.
import app.model.model  # noqa: F401

# Synchronous counterpart of db.py's async URL, from the same psycopg 3 package.
DB_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DB_URL, echo=True)


def reset_database():
    # drop_all() destroys every mapped table and its rows. Fine while the schema
    # is still changing; never run it against real content.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    reset_database()
