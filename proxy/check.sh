#!/usr/bin/env bash
# Asserts the same-origin proxy's routing. --path-as-is stops curl from
# normalising dot segments before nginx sees them.
set -u
BASE="${PROXY_URL:-http://127.0.0.1:8080}"
MARKER="crop-cms-proxy-placeholder-marker"
fails=0
skipped=0

req() { curl -sS --path-as-is -o "$BODY" -D "$HDRS" -w '%{http_code}' "$@"; }
status_of() { req "$BASE$1"; }
ok()   { echo "ok   $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }
check() { # name, condition exit code
  if [ "$2" -eq 0 ]; then ok "$1"; else fail "$1"; fi
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
BODY="$TMP/body"; HDRS="$TMP/hdrs"

code=$(status_of /)
ctype=$(grep -i '^content-type:' "$HDRS" | tr -d '\r')
[ "$code" = 200 ] && [[ "$ctype" == *text/html* ]] && grep -q "$MARKER" "$BODY"
check "/ is 200 text/html with the marker" $?
cp "$BODY" "$TMP/root"

code=$(status_of /some/client/route)
[ "$code" = 200 ] && cmp -s "$BODY" "$TMP/root"
check "client route falls back to / body" $?

code=$(status_of /api/health)
[ "$code" = 200 ] && [ "$(cat "$BODY")" = '{"status":"ok"}' ]
check "/api/health is 200 {\"status\":\"ok\"}" $?

okHome=0
for p in /health /openapi.json; do
  code=$(status_of "$p")
  { [ "$code" = 200 ] && grep -q "$MARKER" "$BODY"; } || okHome=1
done
check "/health and /openapi.json are the placeholder, not the API" $okHome

code=$(status_of /api/nope)
a=1; [ "$code" = 404 ] && [ "$(cat "$BODY")" = '{"detail":"Not Found"}' ] && a=0
code=$(status_of /nope)
b=1; [ "$code" = 200 ] && grep -q "$MARKER" "$BODY" && b=0
check "/api/nope is the API's 404; /nope is the placeholder" $((a + b))

code=$(status_of /api/members/me)
[ "$code" = 401 ] && grep -qi '^www-authenticate: *Bearer' "$HDRS" \
  && [ "$(cat "$BODY")" = '{"detail":"Not authenticated"}' ]
check "/api/members/me is 401 with WWW-Authenticate: Bearer" $?

okReady=0
for p in /api/health/ready /api/health/ready/ /api/health/%72eady /api/%68ealth/ready \
  /api/health%2fready /api%2fhealth/ready /api/health/ready%2f //api/health/ready \
  /api//health/ready /api/./health/ready /api/x/../health/ready /api/health/ready\;x \
  /api/health/ready. /api/health/ready%20 /%61pi/health/ready /api/health/ready%00 \
  /api/health/READY /API/health/ready; do
  code=$(status_of "$p")
  if [ "$code" = 404 ] || [ "$code" = 400 ] || { [ "$code" = 200 ] && grep -q "$MARKER" "$BODY"; }; then :
  else echo "     $p -> $code"; okReady=1; fi
done
for p in /api/health/ready /api/health/ready/ /api/health/%72eady /api/x/../health/ready; do
  code=$(status_of "$p")
  { [ "$code" = 404 ] && grep -q '<title>404' "$BODY"; } || { echo "     $p not nginx's 404 ($code)"; okReady=1; }
done
check "readiness is never reachable through the proxy" $okReady

okHealth=0
for p in /api/health '/api/health?x=1'; do
  code=$(status_of "$p")
  { [ "$code" = 200 ] && [ "$(cat "$BODY")" = '{"status":"ok"}' ]; } || okHealth=1
done
check "/api/health (with and without a query) is 200 ok" $okHealth

code=$(status_of /api/health/readyx)
[ "$code" = 404 ] && grep -q '<title>404' "$BODY"
check "/api/health/readyx is nginx's own 404" $?

code=$(status_of /api)
a=1; [ "$code" = 301 ] && grep -qi '^location: /api/\s*$' "$HDRS" && a=0
code=$(status_of /api/)
b=1; [ "$code" = 200 ] && [ "$(cat "$BODY")" = '{"Hello":"World"}' ] && b=0
code=$(status_of /apix)
c=1; [ "$code" = 200 ] && grep -q "$MARKER" "$BODY" && c=0
check "/api is 301 to /api/; /api/ is the API root; /apix is the placeholder" $((a + b + c))

code=$(status_of /assets/missing.js)
[ "$code" = 200 ] && grep -q "$MARKER" "$BODY"
check "a missing asset falls back to the placeholder" $?

# nginx -t can only inspect the local compose container, not whatever PROXY_URL points at.
case "${BASE%/}" in http://127.0.0.1:8080 | http://localhost:8080) local_proxy=1 ;; *) local_proxy=0 ;; esac
if [ "$local_proxy" = 1 ]; then
  if out=$(cd "$(dirname "$0")/.." && docker compose exec -T proxy nginx -t 2>&1); then
    ok "nginx -t accepts the config"
  else
    fail "nginx -t accepts the config"
    echo "$out" | sed 's/^/     /'
  fi
else
  echo "skip nginx -t accepts the config (PROXY_URL is not the default local proxy URL)"
  skipped=$((skipped + 1))
fi

if [ "$fails" -eq 0 ]; then
  [ "$skipped" -eq 0 ] && echo "ALL PASS" || echo "ALL PASS ($skipped skipped)"
else
  echo "$fails check(s) failed"
fi
[ "$fails" -eq 0 ]
