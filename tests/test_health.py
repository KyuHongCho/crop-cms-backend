"""Open probe routes: GET /health (liveness, never touches the database) and GET /health/ready
(readiness, SELECT 1 with a 503 when the database is unreachable, slow or erroring)."""
import asyncio
import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.router.health as health_router
from app.db.db import DB_NAME, DB_PASSWORD, DB_USER, get_db
from app.main import app


@pytest.fixture
def broken_db():
    async def failing_get_db():
        raise RuntimeError("the database must not be touched")
        yield

    app.dependency_overrides[get_db] = failing_get_db
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def test_health_is_ok_and_not_cacheable(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_health_never_touches_the_database(client, broken_db):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_open_to_a_member_token_too(client_with_role):
    response = client_with_role("member").get("/health")
    assert response.status_code == 200
    assert "www-authenticate" not in response.headers


def test_health_operation_declares_no_security():
    assert "security" not in app.openapi()["paths"]["/health"]["get"]


def test_health_body_has_exactly_one_key(client):
    assert list(client.get("/health").json()) == ["status"]


@pytest.fixture
def dead_db():
    # Port 1 on localhost refuses at once: an OperationalError without touching any database.
    engine = create_async_engine(f"postgresql+psycopg_async://{DB_USER}:{DB_PASSWORD}@127.0.0.1:1/{DB_NAME}")

    async def dead_get_db():
        async with AsyncSession(engine) as session:
            yield session

    app.dependency_overrides[get_db] = dead_get_db
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def override_execute(execute):
    class StubSession:
        pass

    stub = StubSession()
    stub.execute = execute

    async def stub_get_db():
        yield stub

    app.dependency_overrides[get_db] = stub_get_db


@pytest.fixture(autouse=False)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_ready_is_ok_against_the_database(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_ready_is_503_when_the_database_is_unreachable(client, dead_db):
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/health").status_code == 200


def test_ready_503_leaks_nothing(client, dead_db):
    response = client.get("/health/ready")
    assert response.status_code == 503
    everything = response.text + repr(dict(response.headers))
    for secret in ("127.0.0.1", "psycopg", "Connection refused", DB_USER, DB_PASSWORD):
        assert secret not in everything


def test_ready_logs_only_the_exception_class(client, dead_db, caplog):
    with caplog.at_level("WARNING", logger="app.router.health"):
        client.get("/health/ready")
    text = " ".join(record.getMessage() for record in caplog.records if record.name == "app.router.health")
    assert "OperationalError" in text
    assert "127.0.0.1" not in text
    assert "Connection refused" not in text


def test_ready_times_out_to_503(client, clear_overrides, monkeypatch):
    async def slow_execute(*args, **kwargs):
        await asyncio.sleep(10)

    override_execute(slow_execute)
    monkeypatch.setattr(health_router, "READY_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    response = client.get("/health/ready")
    assert time.monotonic() - started < 2
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_ready_non_database_error_stays_a_500(client, clear_overrides):
    async def broken_execute(*args, **kwargs):
        raise RuntimeError("a bug, not an outage")

    override_execute(broken_execute)
    assert client.get("/health/ready").status_code == 500


def test_ready_operation_is_open_and_declares_503():
    operation = app.openapi()["paths"]["/health/ready"]["get"]
    assert "security" not in operation
    assert "503" in operation["responses"]
