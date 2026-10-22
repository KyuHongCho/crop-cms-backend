#!/usr/bin/env bash
# Runs outside-check.sh against stub curl, openssl, nc and uname, so every row can be made to pass and fail
# with no network. The stubs prove the script's logic, not the real output formats of those tools.
set -u
cd "$(dirname "$0")/.." || exit 1
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
export STUB_LOG="$tmp/calls.log"

cat >"$tmp/bin/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >>"$STUB_LOG"
out="" hdr="" fmt="" insecure=0 url=""
while [ $# -gt 0 ]; do
  case $1 in
    -o) out=$2; shift ;;
    -D) hdr=$2; shift ;;
    -w) fmt=$2; shift ;;
    --max-time | --resolve) shift ;;
    -k) insecure=1 ;;
    -sS) ;;
    -*) echo "stub curl: unexpected option $1" >&2; exit 99 ;;
    *) url=$1 ;;
  esac
  shift
done
scheme=${url%%://*}
rest=${url#*://}
host=${rest%%/*}
path=/${rest#*/}
if [ "${S_DOWN:-0}" = 1 ]; then printf 000; exit 7; fi
if [ "$scheme" = https ] && [ "$insecure" -eq 0 ] && [ "${S_TRUST_EXIT:-0}" -ne 0 ]; then
  printf 000
  exit "$S_TRUST_EXIT"
fi
code=404 loc="" body="" ctype=""
SHELL_HTML='<!doctype html><html><head><script type="module" crossorigin src="/assets/index-STUB.js"></script><link rel="stylesheet" crossorigin href="/assets/index-STUB.css"></head><body><div id="root"></div></body></html>'
case "$scheme:$path" in
  http:/api/health) code=301; loc="https://$host/api/health" ;;
  https:/api/health) code=200; body=${S_HEALTH_BODY-'{"status":"ok"}'} ;;
  https:/api/items/) code=307; loc="https://$host/api/items" ;;
  https:/api/items) code=200; body=${S_ITEMS_BODY-'[{"id":1,"title":"basil"}]'} ;;
  https:/api/retrieval/basil/optimal-temperature)
    code=200
    body=${S_RET_BODY-'{"document_count":3,"documents":[{"read_directly":true},{"read_directly":false},{"read_directly":true}],"dropped":[]}'} ;;
  https:/) code=200; ctype=${S_SHELL_CT-"text/html; charset=utf-8"}; body=${S_SHELL_BODY-$SHELL_HTML} ;;
  https:/library/spa-check) code=200; ctype=${S_SHELL_CT-"text/html; charset=utf-8"}; body=${S_DEEP_BODY-${S_SHELL_BODY-$SHELL_HTML}} ;;
  https:/assets/index-STUB.js) code=200; ctype=${S_JS_CT-"text/javascript; charset=utf-8"}; body='console.log(1)' ;;
  https:/assets/index-STUB.css) code=200; ctype=${S_CSS_CT-"text/css; charset=utf-8"}; body='body{}' ;;
esac
for kv in ${S_CODES:-}; do [ "${kv%%=*}" = "$scheme:$path" ] && code=${kv#*=}; done
for kv in ${S_LOCS:-}; do [ "${kv%%=*}" = "$scheme:$path" ] && loc=${kv#*=}; done
[ -n "$out" ] && printf '%s' "$body" >"$out"
if [ -n "$hdr" ]; then
  {
    printf 'HTTP/1.1 %s X\r\n' "$code"
    [ -n "$loc" ] && printf 'Location: %s\r\n' "$loc"
    if [ -n "$ctype" ]; then
      if [ "${S_HDR_LOWER:-0}" = 1 ]; then printf 'content-type: %s\r\n' "$ctype"; else printf 'Content-Type: %s\r\n' "$ctype"; fi
    fi
    printf '\r\n'
  } >"$hdr"
fi
[ -n "$fmt" ] && printf '%s' "$code"
exit 0
STUB

cat >"$tmp/bin/openssl" <<'STUB'
#!/usr/bin/env bash
echo "openssl $*" >>"$STUB_LOG"
case $1 in
  s_client) [ "${S_NOCERT:-0}" = 1 ] || echo stub-cert ;;
  x509)
    [ "${S_NOCERT:-0}" = 1 ] && exit 1
    iss=${S_ISSUER-"C=US, O=Let's Encrypt, CN=E5"}
    if [ "${S_FMT:-new}" = old ]; then echo "issuer= /$(echo "$iss" | sed 's|, |/|g')"; else echo "issuer=$iss"; fi
    echo "notAfter=Jan  1 00:00:00 2027 GMT" ;;
esac
STUB

cat >"$tmp/bin/nc" <<'STUB'
#!/usr/bin/env bash
echo "nc $*" >>"$STUB_LOG"
for port; do :; done
case " ${S_OPEN_PORTS:-} " in *" $port "*) exit 0 ;; esac
exit 1
STUB

cat >"$tmp/bin/uname" <<'STUB'
#!/usr/bin/env bash
echo "uname $*" >>"$STUB_LOG"
echo "${S_UNAME:-Linux}"
STUB
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH"

fails=0
check() { if [ "$2" = ok ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
ok_if() { if "${@:2}"; then check "$1" ok; else check "$1" no; fi; }

ORDER="issuer trust http-redirect health blocked slash items no-draft retrieval closed"
SPA_ROWS="shell deep-link asset-js asset-css"
D=example.com
DRAFT=INTERNAL-DRAFT-DO-NOT-PUBLISH-BASIL-PROPAGATION-NOTE
STG="C=US, O=(STAGING) Let's Encrypt, CN=(STAGING) Ersatz E1"

run() {
  local envs=()
  while [ "$1" != -- ]; do envs+=("$1"); shift; done
  shift
  : >"$STUB_LOG"
  rc=0
  env ${envs[@]+"${envs[@]}"} bash prod/outside-check.sh "$@" >"$tmp/out" 2>"$tmp/err" || rc=$?
}
rows() { awk '$1=="PASS" || $1=="FAIL" || $1=="SKIP"' "$tmp/out"; }
states() { rows | awk '{printf "%s%s=%s", (NR > 1 ? " " : ""), $2, $1}'; }
expected() {
  local n s o line=""
  for n in ${GROUPS_ORDER:-$ORDER}; do
    s=PASS
    for o in "$@"; do [ "${o%%=*}" = "$n" ] && s=${o#*=}; done
    line="$line$n=$s "
  done
  echo "${line% }"
}
t() {
  local label=$1 wrc=$2 diffs=$3 d=()
  shift 3
  IFS=' ' read -r -a d <<<"$diffs"
  run "$@"
  local got want
  got=$(states)
  want=$(expected ${d[@]+"${d[@]}"})
  if [ "$rc" -eq "$wrc" ] && [ "$got" = "$want" ]; then
    check "$label" ok
  else
    check "$label" no; echo "     rc=$rc want $wrc"; echo "     got  $got"; echo "     want $want"
  fi
}
usage_exit() { run -- "$@"; ok_if "exit 2 for: $*" test "$rc" -eq 2; }

t "strict, everything good: ten PASS rows and exit 0" 0 "" -- "$D"
ok_if "the good run ends with ALL PASS" grep -qx "ALL PASS" "$tmp/out"
ok_if "the good run counts 10 passed, 0 failed, 0 skipped" grep -qx "10 passed, 0 failed, 0 skipped" "$tmp/out"
ok_if "every row has a detail" test "$(rows | awk 'NF < 3' | wc -l)" -eq 0
ok_if "exactly 10 rows (a deleted check shows here)" test "$(rows | wc -l)" -eq 10
ok_if "the rows come in table order, certificate checks first" test "$(rows | awk '{printf "%s ", $2}')" = "$ORDER "

echo "-- tls"
t "issuer says STAGING in strict mode" 1 "issuer=FAIL" S_ISSUER="$STG" -- "$D"
t "issuer from another CA in strict mode" 1 "issuer=FAIL" S_ISSUER="C=US, O=Example Org, CN=Example CA" -- "$D"
t "no certificate readable" 1 "issuer=FAIL" S_NOCERT=1 -- "$D"
t "LibreSSL-style issuer line parses (strict, good)" 0 "" S_FMT=old -- "$D"
run S_FMT=old -- "$D"
ok_if "LibreSSL-style issuer is printed as read" grep -q "issuer: /C=US/O=Let's Encrypt/CN=E5" "$tmp/out"
t "trust fails in strict mode: trust FAIL, rows needing -k SKIP" 1 "trust=FAIL health=SKIP blocked=SKIP slash=SKIP items=SKIP no-draft=SKIP retrieval=SKIP" S_TRUST_EXIT=60 -- "$D"
t "strict run against a staging server: exactly issuer and trust FAIL" 1 "issuer=FAIL trust=FAIL health=SKIP blocked=SKIP slash=SKIP items=SKIP no-draft=SKIP retrieval=SKIP" \
  S_TRUST_EXIT=60 S_ISSUER="$STG" -- "$D"
ok_if "that run has exactly two FAIL rows" test "$(rows | grep -c '^FAIL')" -eq 2
ok_if "every SKIP row says why" test "$(rows | grep '^SKIP' | grep -c 'trust failed')" -eq 6
t "http answers 200 with no redirect" 1 "http-redirect=FAIL" S_CODES="http:/api/health=200" S_LOCS="http:/api/health=" -- "$D"
t "http answers 200 with an https Location" 1 "http-redirect=FAIL" S_CODES="http:/api/health=200" -- "$D"
t "http redirects to http://" 1 "http-redirect=FAIL" S_LOCS="http:/api/health=http://$D/api/health" -- "$D"
t "http redirects to another host" 1 "http-redirect=FAIL" S_LOCS="http:/api/health=https://other.$D/api/health" -- "$D"
t "http redirects to a host that merely starts with the domain" 1 "http-redirect=FAIL" S_LOCS="http:/api/health=https://$D.other.$D/" -- "$D"
for c in 302 307 308; do
  t "http redirect code $c is accepted" 0 "" S_CODES="http:/api/health=$c" -- "$D"
done
t "health answers 500" 1 "health=FAIL" S_CODES="https:/api/health=500" -- "$D"
t "health answers 200 without status ok" 1 "health=FAIL" S_HEALTH_BODY='{"status":"down"}' -- "$D"

echo "-- edge"
for p in /api/health/ready /api/health/ready/ /api/health/ready%2f /api/docs /api/redoc /api/openapi.json; do
  t "blocked path answers 200: $p" 1 "blocked=FAIL" S_CODES="https:$p=200" -- "$D"
done
run S_CODES="https:/api/openapi.json=200" -- "$D"
ok_if "the blocked FAIL row names the path" grep -q "not 404: /api/openapi.json=200" "$tmp/out"
t "slash redirect is 308" 1 "slash=FAIL" S_CODES="https:/api/items/=308" -- "$D"
t "slash redirect goes to http://" 1 "slash=FAIL" S_LOCS="https:/api/items/=http://$D/api/items" -- "$D"
t "slash redirect keeps the slash" 1 "slash=FAIL" S_LOCS="https:/api/items/=https://$D/api/items/" -- "$D"

echo "-- data"
t "items answers 500" 1 "items=FAIL no-draft=FAIL" S_CODES="https:/api/items=500" S_ITEMS_BODY= -- "$D"
t "items answers 500 with a list body" 1 "items=FAIL no-draft=FAIL" S_CODES="https:/api/items=500" -- "$D"
t "items is an empty list" 1 "items=FAIL" S_ITEMS_BODY='[]' -- "$D"
t "items is not a list" 1 "items=FAIL" S_ITEMS_BODY='{"detail":"x"}' -- "$D"
t "items is a pretty-printed non-empty list" 0 "" S_ITEMS_BODY="$(printf '[\n  {\n    "id": 1\n  }\n]')" -- "$D"
t "the draft marker is in the items body" 1 "no-draft=FAIL" S_ITEMS_BODY="[{\"body\":\"$DRAFT: x\"}]" -- "$D"
t "retrieval answers 500" 1 "retrieval=FAIL" S_CODES="https:/api/retrieval/basil/optimal-temperature=500" -- "$D"
t "retrieval reports 2 documents" 1 "retrieval=FAIL" S_RET_BODY='{"document_count":2,"documents":[{"read_directly":true},{"read_directly":true}]}' -- "$D"
t "retrieval count says 3 but lists 2" 1 "retrieval=FAIL" S_RET_BODY='{"document_count":3,"documents":[{"read_directly":true},{"read_directly":true}]}' -- "$D"
t "retrieval lists 3 but reports 2" 1 "retrieval=FAIL" S_RET_BODY='{"document_count":2,"documents":[{"read_directly":true},{"read_directly":false},{"read_directly":true}]}' -- "$D"

echo "-- ports"
for p in 5432 8000 8081; do
  t "port $p answers" 1 "closed=FAIL" S_OPEN_PORTS="$p" -- "$D"
done
run S_OPEN_PORTS=8000 -- "$D"
ok_if "the ports FAIL row names the open port" grep -q "answering: 8000" "$tmp/out"
run S_UNAME=Darwin -- "$D" --only ports
ok_if "Darwin: nc gets -G 5 for all three ports" test "$(grep -c '^nc -zv -G 5 example.com ' "$STUB_LOG")" -eq 3
ok_if "Darwin: nc is never given -w" test "$(grep -c ' -w ' <(grep '^nc ' "$STUB_LOG"))" -eq 0
run S_UNAME=Linux -- "$D" --only ports
ok_if "Linux: nc gets -w 5 for all three ports" test "$(grep -c '^nc -zv -w 5 example.com ' "$STUB_LOG")" -eq 3
ok_if "Linux: nc is never given -G" test "$(grep -c ' -G ' <(grep '^nc ' "$STUB_LOG"))" -eq 0
ok_if "the three ports are 5432, 8000 and 8081" test "$(grep '^nc ' "$STUB_LOG" | awk '{print $NF}' | tr '\n' ' ')" = "5432 8000 8081 "

echo "-- staging"
t "staging, everything good: ten PASS rows" 0 "" S_TRUST_EXIT=60 S_ISSUER="$STG" -- --staging "$D"
ok_if "staging: ten requests use -k" \
  test "$(grep '^curl' "$STUB_LOG" | grep -c ' -k ')" -eq 10
ok_if "staging: exactly one https request without -k (the trust probe)" \
  test "$(grep '^curl' "$STUB_LOG" | grep 'https://' | grep -vc ' -k ')" -eq 1
t "staging against a production-looking issuer" 1 "issuer=FAIL" S_TRUST_EXIT=60 -- --staging "$D"
t "staging with a certificate that curl trusts" 1 "trust=FAIL" S_ISSUER="$STG" -- --staging "$D"
t "staging with the server down: https rows fail, nothing crashes" 1 \
  "trust=FAIL http-redirect=FAIL health=FAIL blocked=FAIL slash=FAIL items=FAIL no-draft=FAIL retrieval=FAIL" S_DOWN=1 S_ISSUER="$STG" -- --staging "$D"
t "strict with the server down: trust FAIL, https rows SKIP" 1 \
  "trust=FAIL http-redirect=FAIL health=SKIP blocked=SKIP slash=SKIP items=SKIP no-draft=SKIP retrieval=SKIP" S_DOWN=1 -- "$D"

echo "-- spa"
GROUPS_ORDER=$SPA_ROWS t "--only spa: four PASS rows" 0 "" -- "$D" --only spa
ok_if "--only spa counts 4 passed, 0 failed, 0 skipped" grep -qx "4 passed, 0 failed, 0 skipped" "$tmp/out"
ok_if "--only spa rows each have a detail" test "$(rows | awk 'NF < 3' | wc -l)" -eq 0
ok_if "--only spa: the asset rows name the files the shell names" \
  test "$(rows | grep -c -e 'for /assets/index-STUB.js$' -e 'for /assets/index-STUB.css$')" -eq 2
GROUPS_ORDER=$SPA_ROWS t "placeholder shell (no app root)" 1 "shell=FAIL asset-js=FAIL asset-css=FAIL" \
  S_SHELL_BODY='<html><body><p>Nothing deployed yet.</p></body></html>' -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "page with a module script but no app root (placeholder wording is irrelevant)" 1 "shell=FAIL" \
  S_SHELL_BODY='<p>Welcome to the app</p><script type="module" src="/assets/index-STUB.js"></script><link href="/assets/index-STUB.css">' -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "shell without a module script tag" 1 "shell=FAIL" \
  S_SHELL_BODY='<div id="root"></div><script src="/assets/index-STUB.js"></script><link href="/assets/index-STUB.css">' -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "shell served as text/plain" 1 "shell=FAIL" S_SHELL_CT="text/plain" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "shell with no content type" 1 "shell=FAIL" S_SHELL_CT= -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "shell answers 500" 1 "shell=FAIL deep-link=FAIL asset-js=FAIL asset-css=FAIL" S_CODES="https:/=500" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "deep link serves a different body" 1 "deep-link=FAIL" S_DEEP_BODY='<div id="root"></div><script type="module" src="/assets/index-OTHER.js"></script>' -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "deep link answers 404" 1 "deep-link=FAIL" S_CODES="https:/library/spa-check=404" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "shell names no script file: asset-js FAILs, not skips" 1 "asset-js=FAIL" \
  S_SHELL_BODY='<div id="root"></div><script type="module">1</script><link rel="stylesheet" href="/assets/index-STUB.css">' -- "$D" --only spa
ok_if "the empty-extraction row is a FAIL that says so" grep -q "^FAIL asset-js .*names no /assets/\*.js file" "$tmp/out"
GROUPS_ORDER=$SPA_ROWS t "shell names no stylesheet: asset-css FAILs, not skips" 1 "asset-css=FAIL" \
  S_SHELL_BODY='<div id="root"></div><script type="module" src="/assets/index-STUB.js"></script>' -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "a modulepreload link is not the script file" 1 "asset-js=FAIL" \
  S_SHELL_BODY='<div id="root"></div><script type="module">1</script><link rel="modulepreload" href="/assets/index-STUB.js"><link rel="stylesheet" href="/assets/index-STUB.css">' -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "js served as application/octet-stream" 1 "asset-js=FAIL" S_JS_CT="application/octet-stream" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "js served as text/html (the fallback page)" 1 "asset-js=FAIL" S_JS_CT="text/html; charset=utf-8" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "js served with no content type" 1 "asset-js=FAIL" S_JS_CT= -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "js answers 404" 1 "asset-js=FAIL" S_CODES="https:/assets/index-STUB.js=404" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "lowercase header names (HTTP/2 style, CRLF endings) are read" 0 "" S_HDR_LOWER=1 -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "a mixed-case media type is accepted" 0 "" S_JS_CT="Text/JavaScript; charset=UTF-8" S_CSS_CT="TEXT/CSS" S_SHELL_CT="Text/HTML" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "js as application/javascript is accepted" 0 "" S_JS_CT="application/javascript" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "js without a charset suffix is accepted" 0 "" S_JS_CT="text/javascript" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "css served as application/octet-stream" 1 "asset-css=FAIL" S_CSS_CT="application/octet-stream" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "css served as text/html" 1 "asset-css=FAIL" S_CSS_CT="text/html" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "css served with no content type" 1 "asset-css=FAIL" S_CSS_CT= -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "css served as text/javascript" 1 "asset-css=FAIL" S_CSS_CT="text/javascript" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "css answers 404" 1 "asset-css=FAIL" S_CODES="https:/assets/index-STUB.css=404" -- "$D" --only spa
GROUPS_ORDER=$SPA_ROWS t "server down: every spa row FAILs" 1 "shell=FAIL deep-link=FAIL asset-js=FAIL asset-css=FAIL" S_DOWN=1 -- "$D" --only spa
GROUPS_ORDER="issuer trust http-redirect health closed shell deep-link asset-js asset-css" t "--only ports,spa,tls: tls first, spa last" 0 "" -- --only ports,spa,tls "$D"
ok_if "that order puts the spa rows after closed" test "$(rows | awk '{printf "%s ", $2}')" = "issuer trust http-redirect health closed shell deep-link asset-js asset-css "
run -- "$D" --only spa
ok_if "--only spa: four requests, none with -k" \
  test "$(grep -c '^curl' "$STUB_LOG")" -eq 4 -a "$(grep '^curl' "$STUB_LOG" | grep -c ' -k ')" -eq 0
ok_if "--only spa: the requests are / , the deep link and the two assets" \
  test "$(grep '^curl' "$STUB_LOG" | awk '{print $NF}' | tr '\n' ' ')" = "https://example.com/ https://example.com/library/spa-check https://example.com/assets/index-STUB.js https://example.com/assets/index-STUB.css "
GROUPS_ORDER=$SPA_ROWS t "--staging --only spa: four PASS rows" 0 "" S_TRUST_EXIT=60 S_ISSUER="$STG" -- --staging "$D" --only spa
ok_if "--staging --only spa: four requests, all with -k" \
  test "$(grep -c '^curl' "$STUB_LOG")" -eq 4 -a "$(grep '^curl' "$STUB_LOG" | grep -c ' -k ')" -eq 4
GROUPS_ORDER=$SPA_ROWS t "strict --only spa against a staging certificate: every row FAILs" 1 "shell=FAIL deep-link=FAIL asset-js=FAIL asset-css=FAIL" S_TRUST_EXIT=60 -- "$D" --only spa
GROUPS_ORDER="issuer trust http-redirect health shell deep-link asset-js asset-css" t "strict --only tls,spa, trust fails: spa rows SKIP" 1 "trust=FAIL health=SKIP shell=SKIP deep-link=SKIP asset-js=SKIP asset-css=SKIP" S_TRUST_EXIT=60 -- "$D" --only tls,spa
ok_if "that run made no request for the spa paths" test "$(grep '^curl' "$STUB_LOG" | grep -c -e 'spa-check' -e '/assets/' -e 'example.com/ ')" -eq 0
run S_TRUST_EXIT=60 -- "$D" --ip 192.0.2.7 --only spa
ok_if "--ip: the address is not printed by the spa rows, passing or failing" test "$(grep -c 192.0.2.7 "$tmp/out")" -eq 0
run S_SHELL_BODY='<p>x</p>' -- "$D" --ip 192.0.2.7 --only spa
ok_if "--ip: the address is not printed by failing spa rows" test "$(grep -c 192.0.2.7 "$tmp/out")" -eq 0
run -- "$D" --ip 192.0.2.7 --only spa
ok_if "--ip: the address is not printed by passing spa rows" test "$(grep -c 192.0.2.7 "$tmp/out")" -eq 0
usage_exit "$D" --only spa,nonsense
usage_exit "$D" --only spa,

echo "-- arguments"
GROUPS_ORDER="issuer trust http-redirect health closed" t "--only tls,ports: 5 rows" 0 "" -- --only tls,ports "$D"
GROUPS_ORDER="issuer trust http-redirect health" t "--only tls: 4 rows" 0 "" -- "$D" --only tls
GROUPS_ORDER="blocked slash" t "--only edge: 2 rows" 0 "" -- --only edge "$D"
GROUPS_ORDER="items no-draft retrieval" t "--only data: 3 rows" 0 "" -- --only data "$D"
GROUPS_ORDER="closed" t "--only ports: 1 row" 0 "" -- --only ports "$D"
GROUPS_ORDER="issuer trust http-redirect health blocked slash closed" t "--only given in reverse order still runs tls first" 0 "" -- --only ports,edge,tls "$D"
for args in "$D --staging --only tls,edge,ports" "--staging --only tls,edge,ports $D" "--only tls,edge,ports $D --staging" "--only tls,edge,ports --staging $D"; do
  IFS=' ' read -r -a argv <<<"$args"
  run S_TRUST_EXIT=60 S_ISSUER="$STG" -- "${argv[@]}"
  if [ -z "${first:-}" ]; then first=$(cat "$tmp/out"); fi
  ok_if "same table for: $args" test "$(cat "$tmp/out")" = "$first"
done
run S_TRUST_EXIT=60 S_ISSUER="$STG" -- --staging --only tls,edge,ports "$D"
ok_if "that table has no FAIL and no SKIP" test "$(rows | grep -c -e '^FAIL' -e '^SKIP')" -eq 0
usage_exit
usage_exit --staging
usage_exit --nope "$D"
usage_exit "$D" --only nonsense
usage_exit "$D" --only tls,nonsense
usage_exit "$D" --only ""
usage_exit "$D" --only tls,
usage_exit "$D" --only
usage_exit "$D" --ip
usage_exit "$D" --ip not-an-address
usage_exit "$D" other.$D
usage_exit "bad/domain"
usage_exit -x
ok_if "a usage error prints a usage line and no table" test "$(rows | wc -l)" -eq 0

echo "-- address override"
run -- "$D" --ip 192.0.2.7 --only tls,ports
ok_if "--ip: openssl connects to that address with the name as SNI" grep -qx "openssl s_client -connect 192.0.2.7:443 -servername example.com" "$STUB_LOG"
ok_if "--ip: curl maps the name on 443 and 80" grep -q -e "--resolve example.com:443:192.0.2.7 --resolve example.com:80:192.0.2.7" "$STUB_LOG"
ok_if "--ip: nc is pointed at that address" grep -q "^nc -zv -w 5 192.0.2.7 5432" "$STUB_LOG"
ok_if "--ip: the table still passes" test "$rc" -eq 0
ok_if "--ip: the address is not printed in a passing run" test "$(grep -c 192.0.2.7 "$tmp/out")" -eq 0
run S_NOCERT=1 -- "$D" --ip 192.0.2.7 --only tls
ok_if "--ip: the address is not printed when no certificate is read" test "$(grep -c 192.0.2.7 "$tmp/out")" -eq 0
ok_if "--ip: that run still has its FAIL issuer row" grep -q "^FAIL issuer" "$tmp/out"
run S_TRUST_EXIT=60 S_OPEN_PORTS=5432 -- "$D" --ip 192.0.2.7
ok_if "--ip: the address is not printed with a failing trust, skips and an open port" test "$(grep -c 192.0.2.7 "$tmp/out")" -eq 0

echo "-- read-only, no credentials"
run -- "$D" --only tls,edge,data,ports,spa
ok_if "no curl request uses a method, body, upload, credential or header option" \
  test "$(grep '^curl' "$STUB_LOG" | grep -cE ' (-X|-d|-F|-T|-u|-H|-I|--data|--request|--form|--user|--header|--upload-file|--json)')" -eq 0
ok_if "every curl request is an https or http GET of the domain" \
  test "$(grep '^curl' "$STUB_LOG" | grep -cvE ' https?://example\.com/')" -eq 0
ok_if "only curl, openssl, nc and uname are called" \
  test "$(awk '{print $1}' "$STUB_LOG" | sort -u | tr '\n' ' ')" = "curl nc openssl uname "

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
