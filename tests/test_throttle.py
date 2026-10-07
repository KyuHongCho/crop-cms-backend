"""Login throttle: a fixed window of counted attempts per account, kept in login_throttle.
Concurrent TestClients in threads hang, so concurrency is tested at the database level.
"""
import asyncio
import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.auth.throttle as throttle
import app.router.member as member_router
from app.auth.account_key import throttle_key
from app.db import db as app_db
from app.db.migrate_db import engine as sync_engine
from app.main import app
from app.schema.member import MemberLogin
from tests.conftest import DEFAULT_PASSWORD, _import_in_subprocess, add_member, signup_member, sql

URL = "/members/login"
BAD = {"detail": "Incorrect email or password"}
EMAIL = "grower@example.com"
WRONG = "not the password"


def attempt(client, email=EMAIL, password=WRONG):
    # json.dumps escapes a lone surrogate as \ud800, as a real client would; httpx's json= cannot.
    body = json.dumps({"email": email, "password": password})
    return client.post(URL, content=body, headers={"content-type": "application/json"})


def rows():
    return {k: n for k, n in sql("SELECT email_key, attempts FROM login_throttle").all()}


def age(seconds):
    sql("UPDATE login_throttle SET window_started_at = window_started_at - make_interval(secs => :s)", s=seconds)


def run_async(scenario):
    """One fresh engine (default pool) per call, like the other handler-level tests."""
    engine = create_async_engine(app_db.ASYNC_DB_URL)

    async def go():
        return await scenario(engine)

    try:
        return asyncio.run(asyncio.wait_for(go(), 120))
    finally:
        asyncio.run(engine.dispose())


async def handler_login(engine, email=EMAIL, password=DEFAULT_PASSWORD):
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        return await member_router.login(MemberLogin(email=email, password=password), session)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "k" * 64)


@pytest.fixture
def counters(monkeypatch):
    """Wrap verify_password and the member lookup so a test can see whether they ran."""
    calls = {"verify": 0, "lookup": 0}
    real_verify = member_router.verify_password
    real_lookup = member_router.member_crud.get_member_by_email

    async def verify(*args):
        calls["verify"] += 1
        return await real_verify(*args)

    async def lookup(*args):
        calls["lookup"] += 1
        return await real_lookup(*args)

    monkeypatch.setattr(member_router, "verify_password", verify)
    monkeypatch.setattr(member_router.member_crud, "get_member_by_email", lookup)
    return calls


# --- (a) the limit, over HTTP ---------------------------------------------------------------

def test_a_ten_wrong_then_blocked_even_with_the_correct_password(client, counters):
    signup_member(client)
    for _ in range(10):
        response = attempt(client)
        assert (response.status_code, response.json()) == (401, BAD)
    counters.update(verify=0, lookup=0)
    blocked = attempt(client, password=DEFAULT_PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Too many failed attempts"}
    assert 1 <= int(blocked.headers["Retry-After"]) <= 900
    assert counters == {"verify": 0, "lookup": 0}
    assert rows() == {throttle_key(EMAIL): 10}


def test_a_unknown_email_is_counted_and_blocked_the_same(client):
    for _ in range(10):
        assert attempt(client, "nobody@example.com").status_code == 401
    blocked = attempt(client, "nobody@example.com")
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 900


def test_a_success_before_the_limit_deletes_the_row(client):
    signup_member(client)
    for _ in range(5):
        attempt(client)
    assert rows() == {throttle_key(EMAIL): 5}
    assert attempt(client, password=DEFAULT_PASSWORD).status_code == 200
    assert rows() == {}


def test_a_padded_mixed_case_login_with_the_correct_password_succeeds(client):
    signup_member(client)
    response = attempt(client, " GROWER@Example.COM\t", DEFAULT_PASSWORD)
    assert response.status_code == 200
    assert rows() == {}


def test_a_retry_after_tracks_the_remaining_window(client):
    signup_member(client)
    for _ in range(10):
        attempt(client)
    fresh = attempt(client, password=DEFAULT_PASSWORD)
    assert fresh.status_code == 429
    assert 895 <= int(fresh.headers["Retry-After"]) <= 900
    age(600)
    aged = attempt(client, password=DEFAULT_PASSWORD)
    assert aged.status_code == 429
    assert 295 <= int(aged.headers["Retry-After"]) <= 300


def test_a2_case_and_padding_share_one_counter(client):
    signup_member(client)
    spellings = ["Grower@Example.com", " grower@example.com ", "grower@example.com"]
    for i in range(10):
        assert attempt(client, spellings[i % 3]).status_code == 401
    assert attempt(client, " GROWER@example.com\t", DEFAULT_PASSWORD).status_code == 429
    assert list(rows().values()) == [10]


# --- (b) fixed window, simulated time ------------------------------------------------------

def test_b1_attacker_every_second_gets_ten_per_window():
    key = throttle_key(EMAIL)

    async def scenario(engine):
        allowed = []
        for second in range(3600):
            if second:
                async with engine.begin() as conn:
                    await conn.execute(text(
                        "UPDATE login_throttle SET window_started_at = window_started_at - interval '1 second'"
                    ))
            if await throttle.reserve(engine, key) is not None:
                allowed.append(second)
        return allowed

    allowed = run_async(scenario)
    runs, current = [], [allowed[0]]
    for s in allowed[1:]:
        if s == current[-1] + 1:
            current.append(s)
        else:
            runs.append(current)
            current = [s]
    runs.append(current)
    # one run per window; the last may be cut short by the end of the simulation
    assert all(len(r) == 10 for r in runs[:-1]) and len(runs[-1]) <= 10
    assert 4 <= len(runs) <= 5
    assert len(allowed) <= 50


def test_b1_owner_stays_blocked_until_the_window_is_aged_out_or_deleted():
    key = throttle_key(EMAIL)

    async def scenario(engine):
        for _ in range(10):
            assert await throttle.reserve(engine, key) is not None
        assert await throttle.reserve(engine, key) is None
        age(899)
        assert await throttle.reserve(engine, key) is None
        age(1)
        assert await throttle.reserve(engine, key) is not None
        for _ in range(9):
            await throttle.reserve(engine, key)
        assert await throttle.reserve(engine, key) is None
        await throttle.clear(engine, key)
        assert await throttle.reserve(engine, key) is not None

    run_async(scenario)


def test_b2_straddling_a_boundary_allows_fifty_per_five_windows():
    key = throttle_key(EMAIL)

    async def scenario(engine):
        total = 0
        for _ in range(5):
            age(900)
            results = [await throttle.reserve(engine, key) for _ in range(11)]
            assert [r is not None for r in results] == [True] * 10 + [False]
            total += 10
        return total

    assert run_async(scenario) == 50


def test_b2_exactly_the_window_restarts_it_and_one_second_less_does_not():
    key = throttle_key(EMAIL)

    async def scenario(engine):
        for _ in range(10):
            await throttle.reserve(engine, key)
        age(899)
        assert await throttle.reserve(engine, key) is None
        age(1)
        restarted = await throttle.reserve(engine, key)
        assert restarted is not None and restarted.attempts == 1

    run_async(scenario)


# --- (c) concurrency at the database level -------------------------------------------------

def test_c1_fifty_concurrent_reserves_allow_exactly_ten():
    key = throttle_key(EMAIL)

    async def scenario(engine):
        return await asyncio.gather(*(throttle.reserve(engine, key) for _ in range(50)))

    results = run_async(scenario)
    assert sum(r is not None for r in results) == 10
    assert rows() == {key: 10}


def test_c2_thirty_threads_on_their_own_connections_allow_exactly_ten():
    key = throttle_key(EMAIL)
    engine = create_engine(sync_engine.url, poolclass=NullPool)
    barrier = threading.Barrier(30)
    got = []

    def worker():
        with engine.connect() as conn:
            barrier.wait(30)
            row = conn.execute(
                throttle.RESERVE_SQL,
                {"key": key, "window": throttle.LOGIN_WINDOW_SECONDS, "max": throttle.LOGIN_MAX_FAILURES},
            ).first()
            conn.commit()
            got.append(row is not None)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
    finally:
        engine.dispose()
    assert len(got) == 30
    assert sum(got) == 10
    assert rows() == {key: 10}


# --- (d) which events count, pruning, input bounds ----------------------------------------

def test_d_failures_create_one_row_per_key_with_the_right_count(client):
    signup_member(client)
    for _ in range(3):
        attempt(client)
    for _ in range(2):
        attempt(client, "nobody@example.com")
    assert rows() == {throttle_key(EMAIL): 3, throttle_key("nobody@example.com"): 2}


def test_d_expired_rows_are_pruned_by_other_keys_reserves():
    sql(
        "INSERT INTO login_throttle (email_key, attempts, window_started_at) "
        "SELECT md5(g::text), 1, now() - interval '1 hour' FROM generate_series(1, 50) g"
    )
    live = throttle_key("live@example.com")

    async def scenario(engine):
        await throttle.reserve(engine, live)
        assert len(rows()) > 1
        await throttle.reserve(engine, throttle_key("other@example.com"))
        await throttle.reserve(engine, throttle_key("other@example.com"))

    run_async(scenario)
    assert set(rows()) == {live, throttle_key("other@example.com")}


@pytest.mark.parametrize(
    "email, password",
    [("a" * 256, "x"), ("a\x00b@example.com", "x"), (EMAIL, "p" * 129)],
    ids=["email-256", "email-nul", "password-129"],
)
def test_d_invalid_input_is_422_and_creates_no_row(client, email, password):
    assert attempt(client, email, password).status_code == 422
    assert rows() == {}


def test_d_255_characters_that_lowercase_to_510_are_accepted(client):
    email = "İ" * 255
    assert attempt(client, email).status_code == 401
    assert rows() == {throttle_key(email): 1}


@pytest.fixture
def real_client():
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def reserve_calls(monkeypatch):
    calls = []
    real = throttle.reserve

    async def spy(*args):
        calls.append(args)
        return await real(*args)

    monkeypatch.setattr(throttle, "reserve", spy)
    return calls


@pytest.mark.parametrize(
    "email, password",
    [
        ("\ud800", "x"),
        (EMAIL, "pass\ud800word"),
        ("a" * 254 + "\ud800", "x"),
        ("a" * 256, "x"),
    ],
    ids=["s1-email", "s2-password", "s2b-255-surrogate", "s2b-256"],
)
def test_d_surrogates_and_overlong_email_are_422_through_the_real_app(
    real_client, reserve_calls, email, password
):
    assert attempt(real_client, email, password).status_code == 422
    assert rows() == {}
    assert reserve_calls == []


def test_d_s3_astral_emoji_email_is_a_401_with_one_row(real_client):
    assert attempt(real_client, "😀@example.com").status_code == 401
    assert rows() == {throttle_key("😀@example.com"): 1}


# --- (e) failures that must not, or must, cost an attempt ----------------------------------

@pytest.mark.parametrize("secret", [None, ""], ids=["unset", "empty"])
def test_e1_a_failure_after_a_correct_password_consumes_nothing(client, monkeypatch, secret):
    signup_member(client)
    if secret is None:
        monkeypatch.delenv("SECRET_KEY")
    else:
        monkeypatch.setenv("SECRET_KEY", secret)
    for i in range(12):
        assert attempt(client, password=DEFAULT_PASSWORD).status_code == 500
        if i == 0:
            assert rows() == {}
    monkeypatch.setenv("SECRET_KEY", "k" * 64)
    assert attempt(client, password=DEFAULT_PASSWORD).status_code == 200


def test_e2_a_lookup_that_raises_gives_the_attempt_back(client, monkeypatch):
    signup_member(client)

    async def boom(*args):
        raise RuntimeError("lookup down")

    with monkeypatch.context() as m:
        m.setattr(member_router.member_crud, "get_member_by_email", boom)
        for _ in range(12):
            assert attempt(client, password=DEFAULT_PASSWORD).status_code == 500
    assert rows() == {throttle_key(EMAIL): 0}
    assert attempt(client, password=DEFAULT_PASSWORD).status_code == 200


def test_e2b_a_failing_release_is_logged_and_the_lookup_error_propagates(monkeypatch, caplog):
    class LookupBoom(Exception):
        pass

    class ReleaseBoom(Exception):
        pass

    async def lookup(*args):
        raise LookupBoom()

    async def release(*args):
        raise ReleaseBoom()

    monkeypatch.setattr(member_router.member_crud, "get_member_by_email", lookup)
    monkeypatch.setattr(throttle, "release", release)

    async def scenario(engine):
        with pytest.raises(LookupBoom):
            await handler_login(engine)

    with caplog.at_level("ERROR"):
        run_async(scenario)
    assert any(r.name == "app.router.member" and "ReleaseBoom" in caplog.text for r in caplog.records)
    assert rows() == {throttle_key(EMAIL): 1}


def test_e3_a_verify_that_raises_keeps_the_reservation(client, monkeypatch):
    async def boom(*args):
        raise RuntimeError("argon2 died")

    monkeypatch.setattr(member_router, "verify_password", boom)
    for _ in range(10):
        assert attempt(client).status_code == 500
    assert rows() == {throttle_key(EMAIL): 10}
    assert attempt(client).status_code == 429


def test_e4_inactive_member_with_the_correct_password_keeps_the_attempt(client):
    signup_member(client)
    sql("UPDATE members SET is_active = false")
    for _ in range(10):
        response = attempt(client, password=DEFAULT_PASSWORD)
        assert (response.status_code, response.json()) == (401, BAD)
    assert rows() == {throttle_key(EMAIL): 10}
    assert attempt(client, password=DEFAULT_PASSWORD).status_code == 429


def test_e4_an_active_success_after_nine_wrong_attempts_deletes_the_row(client):
    signup_member(client)
    for _ in range(9):
        attempt(client)
    assert attempt(client, password=DEFAULT_PASSWORD).status_code == 200
    assert rows() == {}


# --- (g) cancellation never gives an attempt back ---------------------------------------

def test_g_cancelling_during_the_lookup_or_during_verify_keeps_the_attempt(monkeypatch):
    add_member(email=EMAIL)

    async def lookup_scenario(engine):
        entered = asyncio.Event()

        async def hang(*args):
            entered.set()
            await asyncio.sleep(3600)

        with monkeypatch.context() as m:
            m.setattr(member_router.member_crud, "get_member_by_email", hang)
            for _ in range(10):
                entered.clear()
                task = asyncio.create_task(handler_login(engine))
                await asyncio.wait_for(entered.wait(), 10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert rows() == {throttle_key(EMAIL): 10}
        with pytest.raises(HTTPException) as blocked:
            await handler_login(engine)
        assert blocked.value.status_code == 429

    run_async(lookup_scenario)
    sql("TRUNCATE login_throttle")

    async def verify_scenario(engine):
        entered = asyncio.Event()

        async def hang(*args):
            entered.set()
            await asyncio.sleep(3600)

        with monkeypatch.context() as m:
            m.setattr(member_router, "verify_password", hang)
            for _ in range(10):
                entered.clear()
                task = asyncio.create_task(handler_login(engine))
                await asyncio.wait_for(entered.wait(), 10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert rows() == {throttle_key(EMAIL): 10}
        with pytest.raises(HTTPException) as blocked:
            await handler_login(engine)
        assert blocked.value.status_code == 429

    run_async(verify_scenario)


def test_g_cancelling_during_reserve_never_reaches_verify(monkeypatch):
    called = []
    entered = None

    async def hang(*args):
        entered.set()
        await asyncio.sleep(3600)

    async def verify(*args):
        called.append(1)
        return True

    monkeypatch.setattr(throttle, "reserve", hang)
    monkeypatch.setattr(member_router, "verify_password", verify)

    async def scenario(engine):
        nonlocal entered
        entered = asyncio.Event()
        task = asyncio.create_task(handler_login(engine))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run_async(scenario)
    assert called == []


# --- (h) a release only applies to the window it reserved in --------------------------------

def test_h_release_matches_the_window_not_just_the_key():
    key = throttle_key(EMAIL)

    async def scenario(engine):
        for _ in range(3):
            w1 = (await throttle.reserve(engine, key)).window_id
        sql("DELETE FROM login_throttle")
        second = await throttle.reserve(engine, key)
        assert second.window_id != w1 and second.attempts == 1
        await throttle.release(engine, key, w1)
        assert rows() == {key: 1}
        await throttle.release(engine, throttle_key("none@example.com"), w1)
        assert rows() == {key: 1}

        age(901)
        restarted = await throttle.reserve(engine, key)
        assert restarted.window_id != second.window_id and restarted.attempts == 1
        await throttle.release(engine, key, second.window_id)
        assert rows() == {key: 1}

        live = await throttle.reserve(engine, key)
        assert live.attempts == 2
        await throttle.release(engine, key, live.window_id)
        assert rows() == {key: 1}
        await throttle.release(engine, key, live.window_id)
        await throttle.release(engine, key, live.window_id)
        assert rows() == {key: 0}

    run_async(scenario)


# --- (i) the request holds at most one pooled connection at a time -------------------------

def test_i_twenty_concurrent_correct_logins_do_not_exhaust_the_pool(monkeypatch):
    for i in range(20):
        add_member(email=f"m{i}@example.com")

    async def slow_verify(plain, hashed):
        await asyncio.sleep(0.2)
        return True

    monkeypatch.setattr(member_router, "verify_password", slow_verify)

    async def scenario(engine):
        return await asyncio.gather(
            *(handler_login(engine, f"m{i}@example.com", "pw") for i in range(20))
        )

    started = time.monotonic()
    tokens = run_async(scenario)
    assert all(t.access_token for t in tokens) and len(tokens) == 20
    assert time.monotonic() - started < 15
    assert rows() == {}


# --- (k) fail-closed -------------------------------------------------------------------------

def test_k_a_failing_reserve_is_a_500_with_no_lookup_and_no_verify(client, monkeypatch, counters):
    async def boom(*args):
        raise RuntimeError("throttle table missing")

    monkeypatch.setattr(throttle, "reserve", boom)
    assert attempt(client, password=DEFAULT_PASSWORD).status_code == 500
    assert counters == {"verify": 0, "lookup": 0}


# --- (l) settings are validated at import ---------------------------------------------------

_IMPORT = "import app.auth.throttle"


def test_l0_valid_environment_imports_cleanly():
    result = _import_in_subprocess(_IMPORT)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "name, value",
    [("LOGIN_MAX_FAILURES", "0"), ("LOGIN_WINDOW_SECONDS", "abc")],
    ids=["max-failures-0", "window-abc"],
)
def test_l_a_bad_setting_stops_the_import_and_names_the_variable(name, value):
    result = _import_in_subprocess(_IMPORT, {name: value})
    assert result.returncode != 0
    # the last line is the exception message; the traceback above it names the variable regardless.
    assert name in result.stderr.strip().splitlines()[-1]


def test_l_d_window_above_ten_years_stops_the_import_and_the_cap_itself_is_accepted():
    over = _import_in_subprocess(_IMPORT, {"LOGIN_WINDOW_SECONDS": "315360001"})
    assert over.returncode != 0
    assert "LOGIN_WINDOW_SECONDS" in over.stderr.strip().splitlines()[-1]
    at_cap = _import_in_subprocess(_IMPORT, {"LOGIN_WINDOW_SECONDS": "315360000"})
    assert at_cap.returncode == 0, at_cap.stderr


def test_l_c_the_key_module_does_not_pull_in_the_settings():
    code = "import sys, app.auth.account_key; sys.exit(3 if 'app.auth.throttle' in sys.modules else 0)"
    result = _import_in_subprocess(code)
    assert result.returncode == 0, result.stderr
