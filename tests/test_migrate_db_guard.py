"""reset_database() must refuse once Alembic manages the schema."""
import pytest
from sqlalchemy import text

from app.db import migrate_db


def test_reset_database_refuses_an_alembic_managed_database(monkeypatch):
    with migrate_db.engine.begin() as connection:
        managed = connection.execute(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        ).scalar()
    assert managed, "cms_test must be built by `alembic upgrade head` (see README)"

    def fail_if_reached(*args, **kwargs):
        pytest.fail("reset_database() reached drop_all() on an Alembic-managed database")

    monkeypatch.setattr(migrate_db.Base.metadata, "drop_all", fail_if_reached)
    with pytest.raises(RuntimeError, match="refusing to run"):
        migrate_db.reset_database()
