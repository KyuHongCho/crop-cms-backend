import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

# Set in docker-compose.yaml.
DB_USER = os.getenv("DB_USER", "cms_app")
DB_PASSWORD = os.environ["DB_PASSWORD"]  # secret: no default, fail loudly
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "cms")

# psycopg 3 serves async here and sync in migrate_db.py, so one driver covers both.
ASYNC_DB_URL = f"postgresql+psycopg_async://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# echo=True logs every statement: useful while learning, noisy in production.
engine = create_async_engine(ASYNC_DB_URL, echo=True)

async_session = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
)

# Model classes inherit from this. Define it once: a second declarative_base()
# creates an independent registry, and create_all() on the wrong one does nothing.
Base = declarative_base()


# FastAPI dependency: yields one session per request, closed on the way out.
async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
