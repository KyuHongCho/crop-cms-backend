import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# postgresql+psycopg -> psycopg 3, synchronous.
# The same installed package also serves postgresql+psycopg_async, so moving to
# async later needs no dependency change.
DATABASE_URL = (
    f"postgresql+psycopg://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass
