"""A downgrade drops tables, so alembic refuses it on any database not named *_test.
These runs are offline (--sql), so no database is contacted even when the guard is missing."""
import os
import subprocess

import pytest

from tests.conftest import REPO_ROOT

DOWNGRADE = ["downgrade", "c5e8a2f41d70:02e1a03214cf", "--sql"]


def alembic(args, database, **extra):
    env = {k: v for k, v in os.environ.items() if k != "ALEMBIC_ALLOW_DESTRUCTIVE"}
    env.update(DB_NAME=database, **extra)
    return subprocess.run(["alembic", *args], env=env, capture_output=True, text=True, cwd=str(REPO_ROOT))


def test_a_downgrade_is_refused_on_a_database_that_is_not_a_test_database():
    result = alembic(DOWNGRADE, "cms")
    assert result.returncode != 0
    assert "ALEMBIC_ALLOW_DESTRUCTIVE" in result.stderr + result.stdout
    assert "DROP TABLE" not in result.stdout


def test_the_opt_in_lets_a_downgrade_through():
    result = alembic(DOWNGRADE, "cms", ALEMBIC_ALLOW_DESTRUCTIVE="1")
    assert result.returncode == 0, result.stderr
    assert "DROP TABLE login_throttle" in result.stdout


def test_a_downgrade_on_a_test_database_needs_no_opt_in():
    assert alembic(DOWNGRADE, "cms_test").returncode == 0


def test_an_upgrade_is_never_blocked():
    result = alembic(["upgrade", "02e1a03214cf:c5e8a2f41d70", "--sql"], "cms")
    assert result.returncode == 0, result.stderr


def assert_refused(result):
    assert result.returncode != 0
    assert "ALEMBIC_ALLOW_DESTRUCTIVE" in result.stderr + result.stdout
    assert "DROP TABLE" not in result.stdout


@pytest.mark.parametrize("database", ["cms_testing", "testing_cms", "cmstest", "test"])
def test_only_a_database_name_ending_in_underscore_test_is_exempt(database):
    assert_refused(alembic(DOWNGRADE, database))


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_the_value_1_opts_in(value):
    assert_refused(alembic(DOWNGRADE, "cms", ALEMBIC_ALLOW_DESTRUCTIVE=value))
