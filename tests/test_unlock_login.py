"""scripts/unlock_login.py: the operator's unlock, one transaction with its audit row.
db-test only (conftest refuses anything else); run through `docker compose exec -e DB_HOST=db-test ...`.
"""
import json
import os

import pytest

import app.auth.account_key as account_key
import scripts.unlock_login as unlock_script
from app.crud.audit import SYSTEM_ACTOR_ID
from app.db.migrate_db import engine as sync_engine
from scripts.unlock_login import unlock_login
from tests.conftest import (
    DEFAULT_PASSWORD, add_member, audit_rows, import_in_subprocess, run_python, signup_member, sql,
)

EMAIL = "grower@x.com"
TEST_DB = {"DB_HOST": "db-test", "DB_NAME": "cms_test"}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "k" * 64)


def login(client, email=EMAIL, password="wrong"):
    return client.post("/members/login", json={"email": email, "password": password})


def lock(client, email=EMAIL):
    for _ in range(10):
        assert login(client, email).status_code == 401
    assert login(client, email, DEFAULT_PASSWORD).status_code == 429


def rows():
    return {k: n for k, n in sql("SELECT email_key, attempts FROM login_throttle").all()}


def run_script(*argv, overrides=None):
    return run_python(["-m", "scripts.unlock_login", *argv], {**TEST_DB, **(overrides or {})})


# --- key ---
def test_the_script_uses_the_same_key_function_as_the_login_handler():
    assert unlock_script.throttle_key is account_key.throttle_key


@pytest.mark.parametrize("sample", [" a@b.c \t", "Admin@Example.com", "a@b.c", "İ" * 255],
                         ids=["padded", "mixed-case", "plain", "dotted-capital-i"])
def test_the_raw_string_that_was_throttled_is_the_one_the_script_clears(client, sample):
    assert login(client, sample).status_code == 401
    assert len(rows()) == 1
    assert unlock_login(sample, sync_engine) == 1
    assert rows() == {}


# --- unlock end to end ---
def test_ten_wrong_logins_then_a_padded_mixed_case_unlock_lets_the_owner_in(client):
    signup_member(client, email=EMAIL)
    lock(client)
    assert unlock_login(" Grower@X.com \t", sync_engine) == 1
    assert rows() == {}
    assert login(client, EMAIL, DEFAULT_PASSWORD).status_code == 200


def test_the_real_command_unlocks_and_prints_exactly_unlocked(client):
    signup_member(client, email=EMAIL)
    lock(client)
    result = run_script(" Grower@X.com \t")
    assert (result.returncode, result.stdout) == (0, "unlocked\n"), result.stderr
    assert "DB_HOST=db-test DB_NAME=cms_test" in result.stderr
    assert rows() == {}
    assert login(client, EMAIL, DEFAULT_PASSWORD).status_code == 200


# --- no-ops and scope ---
def test_an_unknown_address_is_a_harmless_no_op_with_one_audit_row(client):
    login(client, "other@example.com")
    before = rows()
    result = run_script("Typo@Nowhere.com")
    assert (result.returncode, result.stdout) == (0, "nothing to unlock\n"), result.stderr
    assert rows() == before and len(before) == 1
    assert audit_rows() == [(SYSTEM_ACTOR_ID, "unlock", None, {"cleared": 0})]
    assert unlock_login("Typo@Nowhere.com", sync_engine) == 0


def test_a_known_member_with_no_throttle_row_is_cleared_0_with_their_id():
    member = add_member("member", email="known@example.com")
    assert unlock_login(" KNOWN@example.com ", sync_engine) == 0
    assert audit_rows() == [(SYSTEM_ACTOR_ID, "unlock", member, {"cleared": 0})]


def test_only_the_target_is_unlocked(client):
    signup_member(client, email="a@example.com")
    signup_member(client, email="b@example.com")
    lock(client, "a@example.com")
    lock(client, "b@example.com")
    a_id = sql("SELECT id FROM members WHERE email = 'a@example.com'").scalar_one()
    assert unlock_login(" A@Example.com ", sync_engine) == 1
    assert rows() == {account_key.throttle_key("b@example.com"): 10}
    assert audit_rows() == [(SYSTEM_ACTOR_ID, "unlock", a_id, {"cleared": 1})]


# --- atomicity ---
def test_a_failing_audit_row_leaves_the_throttle_row_in_place(client, monkeypatch):
    signup_member(client, email=EMAIL)
    lock(client)
    calls = []

    def boom(*args):
        calls.append(args)
        raise RuntimeError("audit failed")

    monkeypatch.setattr(unlock_script, "build_unlock_event", boom)
    with pytest.raises(RuntimeError, match="audit failed"):
        unlock_login(EMAIL, sync_engine)
    assert len(calls) == 1
    assert rows() == {account_key.throttle_key(EMAIL): 10}
    assert audit_rows() == []


# --- argument handling ---
def test_an_argument_that_is_not_utf8_exits_2_and_writes_nothing(client):
    login(client, "other@example.com")
    before = rows()
    result = run_script(b"\xff")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "DB_HOST" not in result.stderr
    assert rows() == before and audit_rows() == []


# --- audit row ---
def test_the_audit_row_is_actor_0_with_the_cleared_count_and_no_email_or_key(client):
    signup_member(client, email=EMAIL)
    lock(client)
    member = sql("SELECT id FROM members WHERE email = :e", e=EMAIL).scalar_one()
    key = account_key.throttle_key(EMAIL)
    unlock_login(EMAIL, sync_engine)
    found = audit_rows()
    assert found == [(SYSTEM_ACTOR_ID, "unlock", member, {"cleared": 1})]
    assert SYSTEM_ACTOR_ID == 0
    everything = json.dumps([list(row) for row in found])
    assert "example" not in everything and EMAIL not in everything and key not in everything


# --- independence from login settings ---
_IMPORT_THROTTLE = "import app.auth.throttle"


@pytest.mark.parametrize(
    "name, value", [("LOGIN_MAX_FAILURES", "0"), ("LOGIN_WINDOW_SECONDS", "abc")]
)
def test_the_script_works_under_a_login_setting_that_stops_the_app(name, value):
    assert import_in_subprocess(_IMPORT_THROTTLE).returncode == 0, "premise not met: valid env"
    broken = import_in_subprocess(_IMPORT_THROTTLE, {name: value})
    assert broken.returncode != 0, "premise not met: the bad setting did not stop the import"
    # the last line is the exception message; a traceback above it echoes the source line regardless.
    assert name in broken.stderr.strip().splitlines()[-1], "premise not met"
    result = run_script("Typo@Nowhere.com", overrides={name: value})
    assert (result.returncode, result.stdout) == (0, "nothing to unlock\n"), result.stderr


def test_the_script_import_chain_avoids_the_throttle_module():
    code = "import sys, scripts.unlock_login; sys.exit(3 if 'app.auth.throttle' in sys.modules else 0)"
    result = import_in_subprocess(code)
    assert result.returncode == 0, result.stderr


# --- stderr line ---
def test_the_stderr_line_names_the_engines_database_not_the_environment(monkeypatch, capfd):
    monkeypatch.setenv("DB_HOST", "wronghost")
    monkeypatch.setenv("DB_NAME", "wrongdb")
    unlock_login("Typo@Nowhere.com", engine=sync_engine)
    err = capfd.readouterr().err
    line = f"unlock_login: DB_HOST={sync_engine.url.host} DB_NAME={sync_engine.url.database}"
    assert err.strip().splitlines() == [line]
    assert "wronghost" not in err and "wrongdb" not in err
    assert os.environ["DB_PASSWORD"] not in err
