#!/usr/bin/env bash
# Builds the prod stage and checks what only a running container shows. No database is
# needed: /health and the redirect are answered before any query.
set -u
cd "$(dirname "$0")/.."
img="cms-prod-smoke-$$"
cid=""
fails=0
cleanup() {
  # The container's own output is the only reason an early exit leaves behind.
  [ "$fails" -ne 0 ] && [ -n "$cid" ] && docker logs --tail 30 "$cid" 2>&1
  [ -n "$cid" ] && docker rm -f "$cid" >/dev/null 2>&1
  docker rmi -f "$img" >/dev/null 2>&1
}
trap cleanup EXIT
check() { # name, condition exit code
  if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi
}

docker build -q --target prod -t "$img" . >/dev/null
check "the prod stage builds" $?
[ "$fails" -eq 0 ] || { echo FAILED; exit 1; }

uid=$(docker run --rm --entrypoint id "$img" -u)
[ -n "$uid" ] && [ "$uid" != 0 ]
check "the app runs as a non-root user (uid $uid)" $?

cid=$(docker run -d -p 127.0.0.1::8000 -e DB_HOST=nohost -e DB_USER=x -e DB_PASSWORD=x -e SECRET_KEY=x "$img")
port=$(docker port "$cid" 8000/tcp | head -1 | awk -F: '{print $NF}')
# --max-time stops a hung (not refused) connection from blocking; --retry-max-time caps the whole wait.
curl -fs --max-time 5 --retry 30 --retry-delay 1 --retry-max-time 30 --retry-all-errors "http://127.0.0.1:$port/health" >/dev/null
check "the prod container answers /health" $?

# Uvicorn trusts the forwarded scheme and root path only because of its startup flags.
loc=$(curl -sS --max-time 5 -i -H 'X-Forwarded-Proto: https' -H 'Host: demo.example.com' "http://127.0.0.1:$port/items/" \
  | tr -d '\r' | awk 'tolower($1)=="location:"{print $2}')
[ "$loc" = "https://demo.example.com/api/items" ]
check "a trailing-slash redirect keeps /api and https (got: $loc)" $?

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo FAILED; fi
[ "$fails" -eq 0 ]
