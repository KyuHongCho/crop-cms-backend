#!/usr/bin/env bash
# Asserts docker-compose.prod.yaml keeps cms unpublished and the proxy minimal, ACME_CA reaches caddy with a
# production default, .env.prod.example cannot start the stack unfilled, and docs/deploy.md passes its lint.
# Needs docker and python3; no containers start. Dummy secrets render the file (template: env -i).
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
ACME_PROD="https://acme-v02.api.letsencrypt.org/directory"
ACME_STAGING="https://acme-staging-v02.api.letsencrypt.org/directory"
# --env-file /dev/null: compose would otherwise read the repo's own .env for an unset ACME_CA.
render() {
  local out=$1; shift
  "$@" docker compose -f docker-compose.prod.yaml --env-file /dev/null config --format json >"$TMP/$out.json" 2>"$TMP/$out.err" \
    || { echo "FAIL prod compose does not render ($out)"; cat "$TMP/$out.err"; exit 1; }
}
render acme-unset env -u ACME_CA
render acme-empty env ACME_CA=
render acme-staging env "ACME_CA=$ACME_STAGING"
# env -i: the exports above would fill the template's blanks, and compose would find the
# repo's own .env; --env-file replaces that lookup.
envi() { env -i PATH="$PATH" HOME="$HOME" "$@"; }
envi docker compose -f docker-compose.prod.yaml --env-file .env.prod.example config -q 2>"$TMP/tpl.err"
echo $? >"$TMP/tpl.rc"
sed -E 's/^([A-Z_]+)=$/\1=dummy/' .env.prod.example >"$TMP/filled.env"
envi docker compose -f docker-compose.prod.yaml --env-file "$TMP/filled.env" config --format json \
  >"$TMP/filled.json" 2>"$TMP/filled.err" \
  || { echo "FAIL the filled-in template does not render"; cat "$TMP/filled.err"; exit 1; }
# Docker keeps the command in the image, so the Dockerfile's prod stage is what is checked.
awk '/^FROM .* AS prod$/{p=1;next} /^FROM /{p=0} p' Dockerfile >"$TMP/prod-stage"
# `build: .` uses the last stage, so it must stay dev.
awk '/^FROM /{n=$NF} END{print n}' Dockerfile >"$TMP/last-stage"

python3 - "$TMP" "$PWD" "$ACME_PROD" "$ACME_STAGING" <<'PY'
import json, os, re, sys
tmp, repo, acme_prod, acme_staging = sys.argv[1:5]
prod = json.load(open(f"{tmp}/prod.json"))["services"]
dev = json.load(open(f"{tmp}/dev.json"))["services"]
stage = open(f"{tmp}/prod-stage").read()
last_stage = open(f"{tmp}/last-stage").read().strip()
fails = 0

def check(name, cond):
    global fails
    print(("ok   " if cond else "FAIL ") + name)
    fails += 0 if cond else 1

cms, caddy = prod["cms"], prod["caddy"]
check("cms builds the prod target", (cms.get("build") or {}).get("target") == "prod")
check("dev is the last Dockerfile stage (what build: . uses)", last_stage == "dev")
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

def acme(name):
    return json.load(open(f"{tmp}/{name}.json"))["services"]["caddy"]["environment"].get("ACME_CA")

check("ACME_CA is set in the caddy environment", acme("acme-unset") is not None)
check("unset ACME_CA gives the production URL", acme("acme-unset") == acme_prod)
check("empty ACME_CA gives the production URL", acme("acme-empty") == acme_prod)
check("ACME_CA set to the staging URL reaches caddy", acme("acme-staging") == acme_staging)
caddyfile = [l.strip() for l in open(f"{repo}/proxy/Caddyfile")]
check("the Caddyfile's acme_ca default is the production URL",
      "acme_ca {$ACME_CA:" + acme_prod + "}" in caddyfile)

tpl = open(f"{repo}/.env.prod.example").read().splitlines()
assign = re.compile(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=(.*)")
active = [(m[1], m[2].strip()) for m in map(assign.match, tpl) if m]
values = dict(active)
compose_text = open(f"{repo}/docker-compose.prod.yaml").read()
compose_vars = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose_text))
known = set(compose_vars)
known |= set(re.findall(r"^\s+([A-Z][A-Z0-9_]*):\s*$", compose_text, re.M))
known.add("COMPOSE_PROJECT_NAME")
filled = json.load(open(f"{tmp}/filled.json"))
tpl_rc = open(f"{tmp}/tpl.rc").read().strip()
tpl_err = open(f"{tmp}/tpl.err").read()
blanks = ["DB_HOST", "DB_PASSWORD", "SECRET_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SITE_ADDRESS"]

check("the unedited template refuses to start", tpl_rc != "0" and "required variable" in tpl_err)
# The template pins 100000 because the demo uses one shared member account.
check("the filled-in template renders as project cms with its own values",
      filled.get("name") == "cms"
      and filled["services"]["cms"]["environment"]["DB_USER"] == "cms_app"
      and filled["services"]["cms"]["environment"]["TOKENS_BUDGET_DAILY"] == "100000"
      and filled["services"]["caddy"]["environment"].get("ACME_CA") == acme_prod)
check("the template has exactly one active ACME_CA line, the production URL",
      [v for k, v in active if k == "ACME_CA"] == [acme_prod])
stale = sorted({k for k, _ in active} - known)
check("every active template name is one the prod compose reads" + (f" (stale: {stale})" if stale else ""), not stale)
check("the secrets, hosts and domain are blank in the template", all(values.get(b) == "" for b in blanks))

# Commented lines count: the optional variables are documented as `# NAME=default`.
mentioned = set(re.findall(r"^\s*#?\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=", "\n".join(tpl), re.M))
documented = compose_vars | {"COMPOSE_PROJECT_NAME"}
ops = open(f"{repo}/docs/operations.md").read()
table = ops.split("\n## Environment variables", 1)[-1]
rows = set(re.findall(r"^\| `([A-Z][A-Z0-9_]*)`", table, re.M))
miss_tpl, miss_row = sorted(documented - mentioned), sorted(documented - rows)
check("every variable of the prod compose is assigned in the template" + (f" (missing: {miss_tpl})" if miss_tpl else ""), not miss_tpl)
check("every variable of the prod compose has a row in the operations table" + (f" (missing: {miss_row})" if miss_row else ""), not miss_row)

def read(path):
    return open(f"{repo}/{path}").read()

def strip_fences(text):
    return re.sub(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$", "", text, flags=re.M | re.S)

def slugs(text):
    seen, out = {}, set()
    for h in re.findall(r"^ {0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$", strip_fences(text), re.M):
        h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
        base = re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-")
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out

def links(text):
    return re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", strip_fences(text))

def dead_links(path):
    base = os.path.dirname(f"{repo}/{path}")
    bad = []
    for target in links(read(path)):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        file, _, frag = target.partition("#")
        dest = os.path.normpath(os.path.join(base, file)) if file else f"{repo}/{path}"
        if not os.path.isfile(dest) or (frag and frag not in slugs(open(dest).read())):
            bad.append(target)
    return bad

fixture = "# T\n## Under_score x\n## Dup\n```\n## in a fence\n```\n## Dup\n## Dup\n"
check("the anchor fixture resolves an underscore heading and Dup, Dup-1, Dup-2, not a fenced one",
      slugs(fixture) == {"t", "under_score-x", "dup", "dup-1", "dup-2"})

bad = dead_links("docs/deploy.md")
check("every link of docs/deploy.md resolves, fragments included" + (f" (dead: {bad})" if bad else ""), not bad)

COMPOSE_CMD = re.compile(r"\bdocker\s+compose\b")
READ_P = re.compile(r"(^|[;&|\s])read(\s+[^;&|]*)?\s-[a-zA-Z0-9]*p")  # \s: POSIX classes do not work in re
HAS_FILE = re.compile(r"(?<!\S)-f\s+docker-compose\.prod\.yaml(?!\S)")
HAS_ENV = re.compile(r"(?<!\S)--env-file\s+/opt/cms/\.env(?!\S)")
NO_PROJECT = {"version"}
TAKES_ARG = {"-f", "--file", "--env-file", "-p", "--project-name", "--profile", "--project-directory",
             "--ansi", "--parallel", "--progress"}

def fenced_blocks(text):
    blocks, cur, closed = [], None, True
    for n, line in enumerate(text.split("\n"), 1):
        m = re.match(r"^ {0,3}(`{3,}|~{3,})\s*(\S*)(.*)$", line)
        if cur is None:
            if m:
                cur = (m[1], m[2], [])
        elif m and m[1][0] == cur[0][0] and len(m[1]) >= len(cur[0]) and not m[2] and not m[3].strip():
            blocks.append((cur[1], cur[2]))
            cur = None
        else:
            closed = closed and not m  # a fence line inside a fence: a close went missing
            cur[2].append((n, line))
    if cur is not None:
        blocks.append((cur[1], cur[2]))
        closed = False
    return blocks, closed

def logical_lines(lines):
    out, buf, start = [], "", None
    for n, line in lines:
        start = n if start is None else start
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
        else:
            out.append((start, buf + line))
            buf, start = "", None
    return out + ([(start, buf)] if buf else [])

def simple_commands(line):
    parts, cur, quote = [], "", None
    for ch in line:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "|;&":
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    return parts + [cur]

def subcommand(tokens):
    i = 0
    while i < len(tokens):
        if tokens[i] in TAKES_ARG:
            i += 2
        elif tokens[i].startswith("-"):
            i += 1
        else:
            return tokens[i], tokens[i + 1:]
    return "", []

def lint(text):
    blocks, closed = fenced_blocks(text)
    bad, composes = [], 0
    if not closed:
        bad.append(("L4", 0, "a fence is never closed"))
    if not blocks:
        bad.append(("L4", 0, "no fenced block at all"))
    for info, lines in blocks:
        if info.lower() == "text":  # output, not commands
            continue
        for n, line in logical_lines(lines):
            if READ_P.search(line):
                bad.append(("L3", n, line.strip()))
            for part in simple_commands(line):
                m = COMPOSE_CMD.search(part)
                if not m:
                    continue
                composes += 1
                args = part[m.end():]
                tokens = args.split()
                sub, rest = subcommand(tokens)
                if sub not in NO_PROJECT and not (HAS_FILE.search(args) and HAS_ENV.search(args)):
                    bad.append(("L1", n, line.strip()))
                if (sub == "run"
                        or (sub == "down" and any(re.fullmatch(r"-[a-zA-Z]*v[a-zA-Z]*|--volumes", t) for t in rest))
                        or ("--force-recreate" in tokens and "--no-deps" not in tokens)
                        or (sub == "config" and not {"-q", "--quiet"} & set(rest))):
                    bad.append(("L2", n, line.strip()))
    if not composes:
        bad.append(("L4", 0, "no docker compose command found"))
    return bad

def fenced(snippet, info="sh"):
    return f"```{info}\n{snippet}\n```\n"

def rules(snippet, info="sh"):
    return {r for r, _, _ in lint(fenced(snippet, info)) if r != "L4"}

PROD = "docker compose -f docker-compose.prod.yaml --env-file /opt/cms/.env"
for snippet in ("docker compose up -d",
                "docker compose -f docker-compose.prod.yaml up -d",
                "docker compose --env-file /opt/cms/.env up -d",
                "docker compose -f docker-compose.yaml --env-file /opt/cms/.env up -d",
                "docker compose -f docker-compose.prod.yaml --env-file .env up -d",
                "docker compose -f other.yaml -p docker-compose.prod.yaml --env-file /opt/cms/.env up -d",
                "docker compose -f docker-compose.prod.yaml \\\n  up -d",
                f"{PROD} ps && docker compose ps"):
    check(f"L1 flags: {snippet!r}", rules(snippet) == {"L1"})
for snippet in (PROD + " up -d", "docker compose version",
                "docker compose -f docker-compose.prod.yaml \\\n  --env-file /opt/cms/.env \\\n  up -d"):
    check(f"L1 passes: {snippet!r}", "L1" not in rules(snippet))
check("L1 does not lint a text block", not rules("docker compose up -d", "text"))
for snippet in (f"{PROD} run cms alembic upgrade head", f"{PROD} down -v", f"{PROD} down -fv", f"{PROD} down --volumes",
                f"{PROD} up -d --force-recreate caddy", f"{PROD} config"):
    check(f"L2 flags: {snippet!r}", rules(snippet) == {"L2"})
for snippet in (f"{PROD} down", f"{PROD} up -d --force-recreate --no-deps cms", f"{PROD} config -q",
                f"{PROD} config --quiet", f"{PROD} exec -T cms python -m scripts.seed"):
    check(f"L2 passes: {snippet!r}", rules(snippet) == set())
for snippet in ("read -p x V", "read -rsp x V", "read -sp V", 'read -rs -p "x" V', "read -r -s -p x V",
                "read -rp x V", "read -t 5 -p x V", "read -n1 -p x V", 'read -r -d "" -p x V',
                "while read -r -p x l; do :; done"):
    check(f"L3 flags: {snippet!r}", rules(snippet) == {"L3"})
for snippet in ("read -rs PW", "read -r ADMIN_EMAIL", 'read -rs "PW?x"', "read -t 5 PW",
                "while read -r line; do :; done", 'read -d "" line', 'printf "x: "; read -rs PW; echo',
                "read -r a b", "read -n1 k"):
    check(f"L3 passes: {snippet!r}", rules(snippet) == set())
GOOD = fenced(PROD + " ps")
check("L4 passes a balanced document with a compose command", lint(GOOD) == [])
check("L4 flags an unclosed fence", [r for r, _, _ in lint("```sh\n" + PROD + " ps\n")] == ["L4"])
check("L4 flags a missing close that the next fence swallows",
      [r for r, _, _ in lint("```sh\n" + PROD + " ps\n```sh\n" + PROD + " ps\n```\n")] == ["L4"])
check("L4 flags a document with no fence", [r for r, _, _ in lint("no fence here\n")].count("L4") == 2)
check("L4 flags a fenced document with no compose command", [r for r, _, _ in lint(fenced("ls"))] == ["L4"])
check("L4 does not count a text block's compose command", [r for r, _, _ in lint(fenced(PROD + " ps", "text"))] == ["L4"])

found = lint(read("docs/deploy.md"))
for rule, what in (("L1", "every docker compose command names -f docker-compose.prod.yaml and --env-file /opt/cms/.env"),
                   ("L2", "no compose run, no down -v, no --force-recreate without --no-deps, no config without -q"),
                   ("L3", "no read with a -p flag"),
                   ("L4", "balanced fences, at least one fence and one docker compose command")):
    hits = [f"line {n}: {t[:60]}" for r, n, t in found if r == rule]
    check(f"docs/deploy.md ({rule}): {what}" + (f" (found: {hits})" if hits else ""), not hits)
for path in ("README.md", "docs/operations.md", "infra/README.md"):
    ok = any(os.path.normpath(os.path.join(os.path.dirname(f"{repo}/{path}"), t.partition("#")[0])) == f"{repo}/docs/deploy.md"
             for t in links(read(path)) if not re.match(r"[a-z][a-z0-9+.-]*:", t))
    check(f"{path} links to docs/deploy.md", ok)

sys.exit(1 if fails else 0)
PY
status=$?
if [ "$status" -eq 0 ]; then echo "ALL PASS"; else echo "FAILED"; cat "$TMP/prod.err" "$TMP/dev.err"; fi
exit "$status"
