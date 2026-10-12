#!/usr/bin/env bash
# Asserts the same-origin proxy's routing. --path-as-is stops curl from
# normalising dot segments before Caddy sees them.
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

code=$(status_of /api/health)
! grep -qi '^server:' "$HDRS"
check "responses carry no Server header" $?

code=$(status_of /api/members/me)
[ "$code" = 401 ] && grep -qi '^www-authenticate: *Bearer' "$HDRS" \
  && [ "$(cat "$BODY")" = '{"detail":"Not authenticated"}' ]
check "/api/members/me is 401 with WWW-Authenticate: Bearer" $?

okReady=0
for p in /api/health/ready /api/health/ready/ /api/health/readyx \
  /api/health/ready\;x /api/health/ready. /api/health/ready%20 /api/health/ready%00 \
  /api/health/ready%2f /api/health/%72eady /api/%68ealth/ready /api/health%2fready \
  //api/health/ready /api//health/ready /api///health/ready /api/./health/ready \
  /api/x/../health/ready /api/%2e%2e/api/health/ready /API/health/ready \
  /api/health/READY '/api/health/ready?x=1'; do
  code=$(status_of "$p")
  # The proxy's own 404 has an empty body; the API's 404 carries JSON.
  { [ "$code" = 404 ] && [ ! -s "$BODY" ]; } || { echo "     $p -> $code, $(wc -c <"$BODY" | tr -d ' ') body bytes (want 404, empty)"; okReady=1; }
done
check "readiness is always the proxy's own empty 404, never the API or the placeholder" $okReady

okDocs=0
for p in /api/docs /api/docs/ /api/docs/oauth2-redirect /api/redoc /api/redoc/ \
  /api/openapi.json /api/openapi.json/ /api/%6fpenapi.json /API/DOCS '/api/docs?x=1'; do
  code=$(status_of "$p")
  { [ "$code" = 404 ] && [ ! -s "$BODY" ]; } || { echo "     $p -> $code (want 404, empty)"; okDocs=1; }
done
check "docs, redoc and openapi.json are blocked with 404" $okDocs

okHealth=0
for p in /api/health '/api/health?x=1'; do
  code=$(status_of "$p")
  { [ "$code" = 200 ] && [ "$(cat "$BODY")" = '{"status":"ok"}' ]; } || okHealth=1
done
check "/api/health (with and without a query) is 200 ok" $okHealth

code=$(status_of /api)
a=1; [ "$code" = 200 ] && grep -q "$MARKER" "$BODY" && a=0
code=$(status_of /api/)
b=1; [ "$code" = 200 ] && [ "$(cat "$BODY")" = '{"Hello":"World"}' ] && b=0
code=$(status_of /apix)
c=1; [ "$code" = 200 ] && grep -q "$MARKER" "$BODY" && c=0
check "/api and /apix are the placeholder; /api/ is the API root" $((a + b + c))

code=$(status_of /assets/missing.js)
[ "$code" = 200 ] && grep -q "$MARKER" "$BODY"
check "a missing asset falls back to the placeholder" $?

# validate and the header probe run inside the compose container, so they apply
# only to the default local proxy, not whatever PROXY_URL points at.
case "${BASE%/}" in http://127.0.0.1:8080 | http://localhost:8080) local_proxy=1 ;; *) local_proxy=0 ;; esac
if [ "$local_proxy" = 1 ]; then
  cd "$(dirname "$0")/.." || exit 1
  if out=$(docker compose exec -T proxy caddy validate --config /etc/caddy/Caddyfile 2>&1); then
    ok "caddy validate accepts the config"
  else
    fail "caddy validate accepts the config"
    echo "$out" | sed 's/^/     /'
  fi

  # A one-off container (no ports, no deps) adapts the real Caddyfile with the
  # variable set on the host the way a user would; the listener must follow it.
  adapted=$(SITE_ADDRESS=http://:8093 docker compose --profile proxy run --rm --no-deps -T proxy \
    caddy adapt --config /etc/caddy/Caddyfile 2>/dev/null)
  [[ "$adapted" == *'":8093"'* ]] && [[ "$adapted" != *'":80"'* ]]
  check "SITE_ADDRESS set on the host reaches the proxy container" $?

  # A unique query string, so lines from an earlier run cannot satisfy the grep.
  tag="logprobe$RANDOM$$"
  status_of "/api/health?$tag" >/dev/null
  sleep 1
  docker compose logs proxy --since 30s 2>&1 | grep 'http.log.access' | grep -q "$tag"
  check "requests appear in the proxy's access log" $?

  docker compose exec -T proxy wget -q -O /dev/null http://127.0.0.1:8081/healthz
  check "internal liveness endpoint answers" $?

  # Compose prints unset-variable warnings on stderr (always, without a .env), so
  # stderr stays out of the compared stdout and is shown only on failure.
  # The API echoes no headers, so run a copy of the real Caddyfile inside the
  # container with the upstream swapped for a site that prints what it receives.
  probe=$(docker compose exec -T proxy sh -c '
    { printf "{\n\tadmin off\n}\nhttp://:8092 {\n\trespond \"xff={http.request.header.X-Forwarded-For} xfp={http.request.header.X-Forwarded-Proto} xfh={http.request.header.X-Forwarded-Host}\"\n}\n"
      sed -e "s#^{\$SITE_ADDRESS:http://:80}#http://:8091#" -e "s#cms:8000#127.0.0.1:8092#" -e "/^# Internal liveness/,\$d" /etc/caddy/Caddyfile
    } > /tmp/probe.Caddyfile
    caddy run --config /tmp/probe.Caddyfile --adapter caddyfile >/dev/null 2>&1 &
    pid=$!
    i=0; while [ $i -lt 20 ] && ! wget -q -O /dev/null http://127.0.0.1:8091/ 2>/dev/null; do sleep 0.25; i=$((i+1)); done
    wget -q -O - --header "X-Forwarded-For: 6.6.6.6" --header "X-Forwarded-Proto: https" \
      --header "X-Forwarded-Host: evil.test" http://127.0.0.1:8091/api/anything 2>&1
    kill $pid; rm -f /tmp/probe.Caddyfile' 2>"$TMP/probe.err")
  if [ "$probe" = "xff=127.0.0.1 xfp=http xfh=127.0.0.1:8091" ]; then
    ok "spoofed X-Forwarded-* headers are overwritten before the API"
  else
    fail "spoofed X-Forwarded-* headers are overwritten before the API"
    echo "     got: $probe"
    sed 's/^/     stderr: /' "$TMP/probe.err"
  fi
else
  echo "skip caddy validate and the forwarded-header probe (PROXY_URL is not the default local proxy URL)"
  skipped=$((skipped + 1))
fi

if [ "$fails" -eq 0 ]; then
  [ "$skipped" -eq 0 ] && echo "ALL PASS" || echo "ALL PASS ($skipped skipped)"
else
  echo "$fails check(s) failed"
fi
[ "$fails" -eq 0 ]
