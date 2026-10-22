#!/usr/bin/env bash
# Checks a deployed domain from outside: GET requests only, no login, no paid call, curl, openssl and nc only.
# Prints one row per check and exits 1 if any row failed, 2 on bad usage.
# The spa group is opt-in; with --only spa alone on a staging certificate, trust is not run, so pass --staging.
set -u
cd "$(dirname "$0")/.." || exit 1

usage() {
  echo "usage: $0 <domain> [--staging] [--ip <ipv4>] [--only tls,edge,data,ports,spa]" >&2
  echo "  spa is opt-in: it runs only when named. --only spa alone on a staging certificate needs --staging." >&2
  exit 2
}

domain=""
staging=0
ip=""
only="tls,edge,data,ports"
while [ $# -gt 0 ]; do
  case $1 in
    --staging) staging=1 ;;
    --ip) [ $# -ge 2 ] || usage; ip=$2; shift ;;
    --only) [ $# -ge 2 ] || usage; only=$2; shift ;;
    -*) usage ;;
    *) [ -z "$domain" ] || usage; domain=$1 ;;
  esac
  shift
done
case $domain in "" | .* | *[!A-Za-z0-9.-]*) usage ;; esac
if [ -n "$ip" ]; then
  printf '%s\n' "$ip" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || usage
fi
case $only in "" | ,* | *, | *,,*) usage ;; esac
rest=$only
while [ -n "$rest" ]; do
  group=${rest%%,*}
  case $group in tls | edge | data | ports | spa) ;; *) usage ;; esac
  if [ "$rest" = "$group" ]; then rest=""; else rest=${rest#*,}; fi
done
want() { case ",$only," in *",$1,"*) return 0 ;; esac; return 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
resolve=()
[ -n "$ip" ] && resolve=(--resolve "$domain:443:$ip" --resolve "$domain:80:$ip")
host=${ip:-$domain}
base="https://$domain"
passes=0 fails=0 skips=0
https_usable=1
items_code=none
DRAFT_MARKER="INTERNAL-DRAFT-DO-NOT-PUBLISH-BASIL-PROPAGATION-NOTE"

row() {
  printf '%-4s %-13s %s\n' "$1" "$2" "$3"
  case $1 in PASS) passes=$((passes + 1)) ;; FAIL) fails=$((fails + 1)) ;; SKIP) skips=$((skips + 1)) ;; esac
}
fetch() {
  local url=$1
  shift
  : >"$tmp/h"
  : >"$tmp/b"
  code=$(curl -sS --max-time 10 ${resolve[@]+"${resolve[@]}"} "$@" -D "$tmp/h" -o "$tmp/b" -w '%{http_code}' "$url" 2>/dev/null)
  crc=$?
}
get() { # a staging certificate is expected to be untrusted, so its rows go through -k
  if [ "$staging" -eq 1 ]; then fetch "$1" -k; else fetch "$1"; fi
}
location() { tr -d '\r' <"$tmp/h" | awk 'tolower($1)=="location:"{print $2; exit}'; }
needs_https() { # name: in strict mode an untrusted certificate would fail every row for one reason
  [ "$https_usable" -eq 1 ] && return 0
  row SKIP "$1" "skipped: trust failed, so this request would need -k"
  return 1
}

check_issuer() {
  # s_client does not verify the chain, so this still reports the issuer when trust fails.
  openssl s_client -connect "$host:443" -servername "$domain" </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -enddate >"$tmp/x" 2>/dev/null
  local iss end
  iss=$(sed -n 's/^issuer= *//p' "$tmp/x")
  end=$(sed -n 's/^notAfter=//p' "$tmp/x")
  if [ -z "$iss" ]; then
    row FAIL issuer "no certificate read from $domain:443"
    return
  fi
  local detail="issuer: $iss; notAfter: $end"
  if [ "$staging" -eq 1 ]; then
    case $iss in *STAGING*) row PASS issuer "$detail" ;; *) row FAIL issuer "expected a STAGING issuer; $detail" ;; esac
  else
    case $iss in
      *STAGING*) row FAIL issuer "STAGING certificate on a strict run; $detail" ;;
      *"Let's Encrypt"*) row PASS issuer "$detail" ;;
      *) row FAIL issuer "not a Let's Encrypt issuer; $detail" ;;
    esac
  fi
}
check_trust() {
  fetch "$base/api/health"
  if [ "$staging" -eq 1 ]; then
    if [ "$crc" -eq 60 ]; then
      row PASS trust "curl exit 60: the staging certificate is untrusted, as expected"
    else
      row FAIL trust "expected curl exit 60 (untrusted), got $crc"
    fi
  elif [ "$crc" -eq 0 ]; then
    row PASS trust "curl accepted the certificate without -k"
  else
    https_usable=0
    row FAIL trust "curl exit $crc without -k"
  fi
}
check_redirect() {
  # Scheme and host only: the path a redirect keeps is not part of what must hold.
  fetch "http://$domain/api/health"
  local loc
  loc=$(location)
  case $code in
    301 | 302 | 307 | 308) ;;
    *) row FAIL http-redirect "expected a redirect, got $code (curl exit $crc)"; return ;;
  esac
  case $loc in
    "https://$domain" | "https://$domain/"*) row PASS http-redirect "$code to $loc" ;;
    *) row FAIL http-redirect "$code to '$loc', expected https://$domain" ;;
  esac
}
check_health() {
  needs_https health || return
  get "$base/api/health"
  if [ "$code" = 200 ] && grep -Eq '"status" ?: ?"ok"' "$tmp/b"; then
    row PASS health "200 and status ok"
  else
    row FAIL health "got $code (curl exit $crc), body without status ok"
  fi
}
check_blocked() {
  needs_https blocked || return
  local p bad=""
  for p in /api/health/ready /api/health/ready/ /api/health/ready%2f /api/docs /api/redoc /api/openapi.json; do
    get "$base$p"
    [ "$code" = 404 ] || bad="$bad $p=$code"
  done
  if [ -z "$bad" ]; then row PASS blocked "6 paths answer 404"; else row FAIL blocked "not 404:$bad"; fi
}
check_slash() {
  needs_https slash || return
  get "$base/api/items/"
  local loc
  loc=$(location)
  if [ "$code" = 307 ] && [ "$loc" = "$base/api/items" ]; then
    row PASS slash "307 to $loc"
  else
    row FAIL slash "got $code to '$loc', expected 307 to $base/api/items"
  fi
}
check_items() {
  needs_https items || return
  get "$base/api/items"
  items_code=$code
  cp "$tmp/b" "$tmp/items"
  if [ "$code" = 200 ] && [ "$(tr -d ' \t\r\n' <"$tmp/items" | head -c 2)" = '[{' ]; then
    row PASS items "200 and a non-empty JSON list"
  else
    row FAIL items "got $code (curl exit $crc), not a non-empty JSON list"
  fi
}
check_no_draft() {
  needs_https no-draft || return
  if [ "$items_code" != 200 ]; then
    row FAIL no-draft "no 200 body of /api/items to inspect"
  elif grep -qF "$DRAFT_MARKER" "$tmp/items"; then
    row FAIL no-draft "the unpublished draft is in /api/items"
  else
    row PASS no-draft "draft marker absent"
  fi
}
check_retrieval() {
  needs_https retrieval || return
  get "$base/api/retrieval/basil/optimal-temperature"
  local n
  n=$(grep -o '"read_directly":' "$tmp/b" | wc -l | tr -d ' ')
  if [ "$code" = 200 ] && grep -Eq '"document_count": ?3[,}]' "$tmp/b" && [ "$n" -eq 3 ]; then
    row PASS retrieval "200 and 3 documents"
  else
    row FAIL retrieval "got $code (curl exit $crc), expected 200 with document_count 3 and 3 documents (found $n)"
  fi
}
check_closed() {
  local flag p open=""
  if ! command -v nc >/dev/null 2>&1; then
    row FAIL closed "nc is not installed"
    return
  fi
  # macOS nc bounds the connect with -G (infra README step 7), other systems with -w.
  case $(uname) in Darwin) flag=-G ;; *) flag=-w ;; esac
  for p in 5432 8000 8081; do
    nc -zv "$flag" 5 "$host" "$p" >/dev/null 2>&1 && open="$open $p"
  done
  if [ -z "$open" ]; then row PASS closed "5432 8000 8081 refuse or time out"; else row FAIL closed "answering:$open"; fi
}
ctype() { # the bare media type of the last response, lowercase, parameters dropped
  tr -d '\r' <"$tmp/h" | awk 'tolower($0) ~ /^content-type:/ { sub(/^[^:]*:/, ""); sub(/;.*/, ""); gsub(/[ \t]/, ""); print tolower($0); exit }'
}
shell_code=none
check_shell() {
  needs_https shell || return
  get "$base/"
  shell_code=$code
  cp "$tmp/b" "$tmp/shell"
  local ct
  ct=$(ctype)
  if [ "$code" = 200 ] && [ "$ct" = text/html ] && grep -qF 'id="root"' "$tmp/shell" && grep -qF '<script type="module"' "$tmp/shell"; then
    row PASS shell "200 text/html with the app root and a module script"
  else
    row FAIL shell "got $code ${ct:-no content type} (curl exit $crc), expected 200 text/html with id=\"root\" and a module script"
  fi
}
check_deep_link() {
  needs_https deep-link || return
  get "$base/library/spa-check"
  if [ "$shell_code" != 200 ]; then
    row FAIL deep-link "no 200 shell body to compare with"
  elif [ "$code" = 200 ] && cmp -s "$tmp/shell" "$tmp/b"; then
    row PASS deep-link "200 and the same page as /"
  else
    row FAIL deep-link "got $code (curl exit $crc), not the same page as /"
  fi
}
check_asset() { # name, attribute, extension, accepted media types: the shell must name a file served as itself
  needs_https "$1" || return
  local path ct
  if [ "$shell_code" != 200 ]; then
    row FAIL "$1" "no 200 shell body to read the .$3 name from"
    return
  fi
  path=$(grep -Eo "$2=\"/assets/[A-Za-z0-9._/-]*\\.$3\"" "$tmp/shell" | head -n 1 | sed 's/^[a-z]*="//; s/"$//')
  if [ -z "$path" ]; then
    row FAIL "$1" "the shell names no /assets/*.$3 file"
    return
  fi
  get "$base$path"
  ct=$(ctype)
  if [ "$code" = 200 ] && [ -n "$ct" ] && case " $4 " in *" $ct "*) true ;; *) false ;; esac; then
    row PASS "$1" "200 $ct for $path"
  else
    row FAIL "$1" "got $code ${ct:-no content type} (curl exit $crc) for $path, expected 200 and one of: $4"
  fi
}
check_asset_js() { check_asset asset-js src js "text/javascript application/javascript"; }
check_asset_css() { check_asset asset-css href css "text/css"; }

echo "outside-check $domain ($([ "$staging" -eq 1 ] && echo staging || echo strict))"
if want tls; then
  check_issuer
  check_trust
  check_redirect
  check_health
fi
if want edge; then
  check_blocked
  check_slash
fi
if want data; then
  check_items
  check_no_draft
  check_retrieval
fi
if want ports; then
  check_closed
fi
if want spa; then
  check_shell
  check_deep_link
  check_asset_js
  check_asset_css
fi

echo "$passes passed, $fails failed, $skips skipped"
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "FAILED"; fi
[ "$fails" -eq 0 ]
