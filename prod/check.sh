#!/usr/bin/env bash
# Asserts docker-compose.prod.yaml keeps cms unpublished and the proxy minimal. Needs docker
# and python3; no containers start. Dummy values stand in for secrets so the file renders.
set -u
cd "$(dirname "$0")/.."
export DB_USER="${DB_USER:-cms_app}" DB_PASSWORD="${DB_PASSWORD:-dummy}" DB_HOST="${DB_HOST:-dummy-host}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-dummy}" SECRET_KEY="${SECRET_KEY:-dummy}"
export SITE_ADDRESS="${SITE_ADDRESS:-example.test}"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
# stderr goes to a file, never into compared output; it is printed only when something fails.
docker compose -f docker-compose.prod.yaml config --format json >"$TMP/prod.json" 2>"$TMP/prod.err" \
  || { echo "FAIL prod compose does not render"; cat "$TMP/prod.err"; exit 1; }
docker compose --profile proxy -f docker-compose.yaml config --format json >"$TMP/dev.json" 2>"$TMP/dev.err" \
  || { echo "FAIL dev compose does not render"; cat "$TMP/dev.err"; exit 1; }
# Docker keeps the command in the image, so the Dockerfile's prod stage is what is checked.
awk '/^FROM .* AS prod$/{p=1;next} /^FROM /{p=0} p' Dockerfile >"$TMP/prod-stage"

python3 - "$TMP" "$PWD" <<'PY'
import json, sys
tmp, repo = sys.argv[1:3]
prod = json.load(open(f"{tmp}/prod.json"))["services"]
dev = json.load(open(f"{tmp}/dev.json"))["services"]
stage = open(f"{tmp}/prod-stage").read()
fails = 0

def check(name, cond):
    global fails
    print(("ok   " if cond else "FAIL ") + name)
    fails += 0 if cond else 1

cms, caddy = prod["cms"], prod["caddy"]
check("cms builds the prod target", (cms.get("build") or {}).get("target") == "prod")
def is_root(user):
    return user.split(":")[0] in ("root", "0")

users = [l.split()[1] for l in stage.splitlines() if l.split()[:1] == ["USER"] and len(l.split()) > 1]
check("the last USER of the prod stage is not root", bool(users) and not is_root(users[-1]))
check("cms has no compose-level root user", not is_root(str(cms.get("user", "app"))))
check("the services are exactly cms and caddy", sorted(prod) == ["caddy", "cms"])
check("only caddy has published ports", all(not s.get("ports") for n, s in prod.items() if n != "caddy"))
check("no service sets network_mode, privileged, cap_add or pid (each defeats the isolation)",
      not any(s.get(k) for s in prod.values() for k in ("network_mode", "privileged", "cap_add", "pid")))
check("cms publishes no port", not cms.get("ports"))
published = sorted((p.get("published"), p.get("protocol")) for p in caddy.get("ports", []))
check("caddy publishes only 80/tcp, 443/tcp, 443/udp",
      published == [("443", "tcp"), ("443", "udp"), ("80", "tcp")])
check("cms command has --root-path /api", "--root-path\", \"/api\"" in stage)
check("cms command has --forwarded-allow-ips", "--forwarded-allow-ips" in stage)
check("cms command has no --reload", "--reload" not in stage)
check("compose does not override the cms command", not cms.get("command") and not cms.get("entrypoint"))
binds = [v for s in prod.values() for v in s.get("volumes", []) if v.get("type") == "bind"]
check("no bind mount of the source tree",
      not any(v["source"].rstrip("/") == repo or v["target"] in ("/src", "/src/.venv") for v in binds)
      and not any(v.get("type") == "bind" for v in cms.get("volumes", [])))
check("cms healthcheck matches the dev one", cms.get("healthcheck") and cms["healthcheck"] == dev["cms"].get("healthcheck"))
check("caddy healthcheck matches the dev proxy one", caddy.get("healthcheck") and caddy["healthcheck"] == dev["proxy"].get("healthcheck"))
sys.exit(1 if fails else 0)
PY
status=$?
if [ "$status" -eq 0 ]; then echo "ALL PASS"; else echo "FAILED"; cat "$TMP/prod.err" "$TMP/dev.err"; fi
exit "$status"
