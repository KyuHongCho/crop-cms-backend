#!/usr/bin/env bash
# Runs db-bootstrap.sql in a throwaway pgvector container, then connects through the app's own
# database URL lines. Needs docker and network (images, pip). Touches no other container: its
# container and network have unique names, publish no port, and are removed on exit.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
PG_IMAGE="${PG_IMAGE:-pgvector/pgvector:pg17}"
PY_IMAGE="${PY_IMAGE:-python:3.13-bookworm}"
name="${BOOTSTRAP_TEST_NAME:-bootstrap-test-$$}"
net="$name-net"
pyname="$name-py"
made_c=0 made_n=0 made_p=0
tmp=$(mktemp -d)

cleanup() {
  [ "$made_c" -eq 1 ] && docker rm -f "$name" >/dev/null 2>&1
  [ "$made_p" -eq 1 ] && docker rm -f "$pyname" >/dev/null 2>&1
  [ "$made_n" -eq 1 ] && docker network rm "$net" >/dev/null 2>&1
  rm -rf "$tmp"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

fails=0
check() { if [ "$2" = ok ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
die() { echo "FAIL $1"; exit 1; }

# A collision fails the check instead of reusing, or later removing, someone else's object.
if docker ps -a --format '{{.Names}}' | grep -qxF -e "$name" -e "$pyname"; then
  die "a container named $name or $pyname already exists; refusing to reuse it"
fi
if docker network ls --format '{{.Name}}' | grep -qxF "$net"; then
  die "a network named $net already exists; refusing to reuse it"
fi

docker network create "$net" >/dev/null || die "could not create network $net"
made_n=1
MASTER_PW=$(openssl rand -hex 16)
POSTGRES_PASSWORD="$MASTER_PW"
export MASTER_PW POSTGRES_PASSWORD
made_c=1
docker run -d --name "$name" --network "$net" -e POSTGRES_DB=cms -e POSTGRES_PASSWORD "$PG_IMAGE" >/dev/null || die "could not start $PG_IMAGE"

ready=0
for _ in $(seq 1 60); do
  if docker exec "$name" psql -U postgres -d cms -h localhost -Atc 'SELECT 1' >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[ "$ready" -eq 1 ] || die "the test database did not become ready"

sup() { docker exec -i -e APP_PW -e NEW_PW "$name" psql -U postgres -d cms -X -v ON_ERROR_STOP=1 "$@"; }

# A space and a quote exercise the psql-variable quoting that a hex password would not.
APP_PW="it's a $(openssl rand -hex 8)"
export APP_PW
if sup -q -f - <"$here/db-bootstrap.sql" >"$tmp/first.log" 2>&1; then
  check "db-bootstrap.sql runs on an empty database" ok
else
  check "db-bootstrap.sql runs on an empty database" no; cat "$tmp/first.log"
fi

PGPASSWORD="$APP_PW"
export PGPASSWORD
app() { docker exec -i -e PGPASSWORD "$name" psql -h "$name" -U cms_app -d cms -X -q -At -v ON_ERROR_STOP=1 "$@"; }

if [ "$(app -c "SELECT extname FROM pg_extension WHERE extname = 'vector'" 2>&1)" = vector ]; then
  check "the vector extension exists" ok
else
  check "the vector extension exists" no
fi
dist=$(app -f - 2>&1 <<'SQL'
CREATE TABLE probe (v vector(3));
INSERT INTO probe VALUES ('[1,2,3]');
SELECT v <=> '[1,2,4]' FROM probe;
DROP TABLE probe;
SQL
)
if printf '%s\n' "$dist" | grep -Eq '^[0-9]+\.[0-9]+$'; then
  check "cms_app can create a vector table and run <=> ($dist)" ok
else
  check "cms_app can create a vector table and run <=>" no; echo "$dist"
fi

if PGPASSWORD="wrong-$(openssl rand -hex 4)" app -c 'SELECT 1' >/dev/null 2>&1; then
  check "a wrong password is refused" no
else
  check "a wrong password is refused" ok
fi

if sup -q -f - <"$here/db-bootstrap.sql" >"$tmp/second.log" 2>&1; then
  check "a second run fails loudly" no
elif grep -q 'role "cms_app" already exists' "$tmp/second.log"; then
  check "a second run fails with: role \"cms_app\" already exists" ok
else
  check "a second run fails with the role message" no; cat "$tmp/second.log"
fi

# The documented reset: the new value reaches psql as a variable, not as a quoted literal.
NEW_PW=$(openssl rand -hex 24)
export NEW_PW
if sup -q -f - >"$tmp/reset.log" 2>&1 <<'SQL'
\getenv new_pw NEW_PW
ALTER ROLE cms_app PASSWORD :'new_pw';
SQL
then
  check "the password reset recipe works" ok
else
  check "the password reset recipe works" no; cat "$tmp/reset.log"
fi

# Imports the app's real URL lines (db.py, migrate_db.py); runs in a one-off image so it does not
# depend on a compose build. The repo is mounted read-only and nothing is written into it.
mkdir "$tmp/py"
cat >"$tmp/py/urlform.py" <<'PY'
import asyncio
from sqlalchemy import text

from app.db.db import engine as async_engine
from app.db.migrate_db import engine as sync_engine

DIST = "SELECT '[1,2,3]'::vector <=> '[1,2,4]'::vector"


async def run_async():
    async with async_engine.connect() as c:
        assert (await c.execute(text("SELECT 1"))).scalar() == 1
        assert 0 <= (await c.execute(text(DIST))).scalar() < 1
    await async_engine.dispose()


asyncio.run(run_async())
with sync_engine.connect() as c:
    assert c.execute(text("SELECT 1")).scalar() == 1
    assert 0 <= c.execute(text(DIST)).scalar() < 1
print("url form ok")
PY
cat >"$tmp/py/setpw.py" <<'PY'
import os
import sys

import psycopg
from psycopg import sql

with psycopg.connect(host=os.environ["DB_HOST"], user="postgres", password=os.environ["MASTER_PW"],
                     dbname="cms", autocommit=True) as c:
    c.execute(sql.SQL("ALTER ROLE cms_app PASSWORD {}").format(sql.Literal(sys.argv[1])))
PY
cat >"$tmp/py/run.sh" <<'SH'
set -e
pip install --quiet --no-cache-dir --target /tmp/deps -r /src/requirements.txt
export PYTHONPATH=/tmp/deps:/src PYTHONDONTWRITEBYTECODE=1
python /t/urlform.py
at_pw="ab@$(python -c 'import secrets; print(secrets.token_hex(8))')"
python /t/setpw.py "$at_pw"
if DB_PASSWORD="$at_pw" python /t/urlform.py >/dev/null 2>&1; then
  echo "an @ in the password did not fail through the URL form"; exit 3
fi
echo "an @ in the password fails through the URL form"
SH
made_p=1
if docker run --rm --name "$pyname" --network "$net" -v "$repo:/src:ro" -v "$tmp/py:/t:ro" \
    -e DB_USER=cms_app -e DB_PASSWORD="$NEW_PW" -e DB_HOST="$name" -e DB_PORT=5432 -e DB_NAME=cms \
    -e MASTER_PW -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_ROOT_USER_ACTION=ignore \
    "$PY_IMAGE" bash /t/run.sh >"$tmp/py.log" 2>&1; then
  check "app.db.db and migrate_db connect with a hex password, and an @ fails" ok
  grep -E '^(url form ok|an @)' "$tmp/py.log"
else
  check "app.db.db and migrate_db connect with a hex password, and an @ fails" no; tail -30 "$tmp/py.log"
fi

if [ "$fails" -eq 0 ]; then echo "bootstrap test: ALL PASS"; else echo "bootstrap test: $fails FAILED"; exit 1; fi
