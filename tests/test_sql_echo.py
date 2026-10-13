"""SQL statement logging is opt-in: SQL_ECHO=1 turns it on, anything else leaves it off."""
import pytest

from tests.conftest import import_in_subprocess

# A fresh interpreter, because both engines are built when their module is first imported.
_PROBE = (
    "import os\n"
    "os.environ.pop('SQL_ECHO', None)\n"
    "{set_env}"
    "from app.db import db, migrate_db\n"
    "print(db.engine.echo, migrate_db.engine.echo)\n"
)


def _echo_flags(set_env=""):
    result = import_in_subprocess(_PROBE.format(set_env=set_env))
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def test_echo_is_off_when_sql_echo_is_unset():
    assert _echo_flags() == ["False", "False"]


@pytest.mark.parametrize("value", ["0", "", "true"])
def test_echo_stays_off_for_any_value_but_1(value):
    assert _echo_flags(f"os.environ['SQL_ECHO'] = {value!r}\n") == ["False", "False"]


def test_echo_is_on_when_sql_echo_is_1():
    assert _echo_flags("os.environ['SQL_ECHO'] = '1'\n") == ["True", "True"]
