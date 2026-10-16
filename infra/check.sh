#!/usr/bin/env bash
# Static checks on infra/: what the firewall may open, that no secret is tracked, and that the
# Terraform files format, validate and refuse a wide ssh_cidr. Needs docker, grep and python3; no
# AWS credentials. `--static` skips the Docker steps.
set -u
cd "$(dirname "$0")" || exit 1
TF_IMAGE="${TF_IMAGE:-hashicorp/terraform:1.16.5}"
SHELLCHECK_IMAGE="${SHELLCHECK_IMAGE:-koalaman/shellcheck:v0.11.0}"
static_only=0
[ "${1:-}" = "--static" ] && static_only=1
fails=0

python3 - "$PWD" <<'PY' || fails=$((fails + 1))
import pathlib, re, subprocess, sys

infra = pathlib.Path(sys.argv[1])
repo = infra.parent
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    fails += 0 if cond else 1


def strip_comments(t):
    out, i, n = [], 0, len(t)
    while i < n:
        c = t[i]
        if c == '"':
            j = i + 1
            while j < n and t[j] != '"':
                j += 2 if t[j] == "\\" else 1
            out.append(t[i:j + 1]); i = j + 1
        elif c == "#" or t.startswith("//", i):
            while i < n and t[i] != "\n":
                i += 1
        elif t.startswith("/*", i):
            j = t.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * t[i:j].count("\n")); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


HEADER = re.compile(r'^[ \t]*([A-Za-z_][\w-]*)((?:[ \t]+"[^"\n]*")*)[ \t]*\{', re.M)


def blocks(text):
    """Blocks found at the top level of text: (kind, labels, body, start, end). None if unbalanced."""
    found, pos = [], 0
    while True:
        m = HEADER.search(text, pos)
        if not m:
            return found
        depth, i = 1, m.end()
        while i < len(text) and depth:
            c = text[i]
            if c == '"':
                i += 1
                while i < len(text) and text[i] != '"':
                    i += 2 if text[i] == "\\" else 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        if depth:
            return None
        labels = re.findall(r'"([^"]*)"', m.group(2))
        found.append((m.group(1), labels, text[m.end():i - 1], m.start(), i))
        pos = i


def attrs(body):
    for _, _, _, s, e in blocks(body) or []:
        body = body[:s] + " " * (e - s) + body[e:]
    return {k: v.strip() for k, v in re.findall(r"^[ \t]*(\w+)[ \t]*=[ \t]*(.*)$", body, re.M)}


tf = {}
for p in sorted(infra.rglob("*.tf")):
    if ".terraform" not in p.parts:
        tf[p.name] = strip_comments(p.read_text())
parsed = {name: blocks(t) for name, t in tf.items()}
check("every .tf file parses (balanced braces)", bool(tf) and all(v is not None for v in parsed.values()))
allb = [b for v in parsed.values() for b in (v or [])]
res = lambda kind: [(l[1], b) for k, l, b, *_ in allb if k == "resource" and l[0] == kind]

# --- firewall ---
ALLOWED = {(22, "tcp"), (80, "tcp"), (443, "tcp"), (443, "udp")}
seen = set()
rules = res("aws_vpc_security_group_ingress_rule")
WEB, DB = "aws_security_group.web.id", "aws_security_group.db.id"
web_rules = [(n, b) for n, b in rules if attrs(b).get("security_group_id") == WEB]
db_rules = [(n, b) for n, b in rules if attrs(b).get("security_group_id") == DB]
check("every ingress rule names the web group or the db group", len(web_rules) + len(db_rules) == len(rules),
      str([n for n, b in rules if attrs(b).get("security_group_id") not in (WEB, DB)]))
check("ingress rules exist (the checks below are not vacuous)", len(web_rules) >= 4)
for name, body in web_rules:
    a = attrs(body)
    proto = a.get("ip_protocol", "").strip('"')
    ports = (a.get("from_port"), a.get("to_port"))
    ok = proto in ("tcp", "udp") and ports[0] == ports[1] and ports[0] and ports[0].isdigit()
    check(f"ingress {name}: one literal port over tcp or udp", ok, f"{proto} {ports}")
    if not ok:
        continue
    key = (int(ports[0]), proto)
    seen.add(key)
    check(f"ingress {name}: port {key[0]}/{proto} is one of 22, 80, 443 (443 may be udp)", key in ALLOWED, str(key))
    if key[0] == 22:
        extra = [k for k in ("cidr_ipv6", "prefix_list_id", "referenced_security_group_id") if k in a]
        check(f"ingress {name}: SSH source is exactly var.ssh_cidr", a.get("cidr_ipv4") == "var.ssh_cidr" and not extra,
              f"cidr_ipv4={a.get('cidr_ipv4')} extra={extra}")
check("SSH is opened by exactly one rule", sum(1 for k in seen if k[0] == 22) == 1 and
      sum(1 for _, b in web_rules if attrs(b).get("from_port") == "22") == 1)
check("80/tcp, 443/tcp and 443/udp are all present", {(80, "tcp"), (443, "tcp"), (443, "udp")} <= seen)
check("the db group has exactly one ingress rule", len(db_rules) == 1, str([n for n, _ in db_rules]))
for name, body in db_rules:
    a = attrs(body)
    wide = [k for k in ("cidr_ipv4", "cidr_ipv6", "prefix_list_id") if k in a]
    check(f"ingress {name}: db rule is tcp 5432 from aws_security_group.web only",
          a.get("ip_protocol") == '"tcp"' and a.get("from_port") == "5432" and a.get("to_port") == "5432"
          and a.get("referenced_security_group_id") == WEB and not wide, f"{a} {wide}")
check("the db group has no egress rule",
      not [n for n, b in res("aws_vpc_security_group_egress_rule") if attrs(b).get("security_group_id") != WEB])
sgs = res("aws_security_group")
check("a security group is defined", len(sgs) >= 1)
for name, body in sgs:
    nested = blocks(body) or []
    inline = ([k for k, _l, *_ in nested if k in ("ingress", "egress")]
              + [l[0] for k, l, *_ in nested if k == "dynamic" and l and l[0] in ("ingress", "egress")]
              + [k for k in attrs(body) if k in ("ingress", "egress")])
    check(f"security group {name}: no inline rules (every rule is a checked resource)", not inline, str(inline))
check("no aws_security_group_rule or network ACL rules sneak in",
      not res("aws_security_group_rule") and not res("aws_network_acl_rule") and not res("aws_network_acl"))
check("no aws_default_security_group (its inline rules would bypass the checks)", not res("aws_default_security_group"))
jsons = [str(p.relative_to(infra)) for p in infra.rglob("*.tf.json") if ".terraform" not in p.parts]
check("no .tf.json files (this checker reads only .tf)", not jsons, str(jsons))
mods = [l[0] if l else "?" for k, l, *_ in allb if k == "module"]
check("no module blocks (resources they create would escape these checks)", not mods, str(mods))

v = {l[0]: b for k, l, b, *_ in allb if k == "variable"}
sv = v.get("ssh_cidr", "")
check("variable ssh_cidr has no default", "ssh_cidr" in v and "default" not in attrs(sv))
check("variable ssh_cidr has a validation that requires /32", any(k == "validation" and '/32$"' in b for k, _, b, *_ in blocks(sv) or []))

# --- instance ---
insts = res("aws_instance")
check("an aws_instance is defined", len(insts) >= 1)
for name, body in insts:
    sub = {k: b for k, _, b, *_ in blocks(body) or []}
    check(f"instance {name}: IMDSv2 required", attrs(sub.get("metadata_options", "")).get("http_tokens") == '"required"')
    check(f"instance {name}: hop limit is 1", attrs(sub.get("metadata_options", "")).get("http_put_response_hop_limit") == "1")
    check(f"instance {name}: attached only to aws_security_group.web",
          attrs(body).get("vpc_security_group_ids") == "[aws_security_group.web.id]" and "security_groups" not in attrs(body)
          and "network_interface" not in sub, attrs(body).get("vpc_security_group_ids", ""))
    check(f"instance {name}: root volume encrypted", attrs(sub.get("root_block_device", "")).get("encrypted") == "true")
    ebs = [b for k, _, b, *_ in blocks(body) or [] if k == "ebs_block_device"]
    check(f"instance {name}: every extra volume is encrypted", all(attrs(b).get("encrypted") == "true" for b in ebs))

# --- database ---
dbi = res("aws_db_instance")
check("exactly one aws_db_instance", len(dbi) == 1, str(len(dbi)))
for name, body in dbi:
    a = attrs(body)
    for k, want in (("engine", '"postgres"'), ("publicly_accessible", "false"), ("storage_encrypted", "true"),
                    ("skip_final_snapshot", "false"), ("final_snapshot_identifier", "var.db_final_snapshot_name"),
                    ("deletion_protection", "var.db_deletion_protection"),
                    ("backup_retention_period", "var.db_backup_retention_days"),
                    ("vpc_security_group_ids", "[aws_security_group.db.id]"),
                    ("parameter_group_name", "aws_db_parameter_group.cms.name"),
                    ("manage_master_user_password", "true")):
        check(f"db instance {name}: {k} = {want}", a.get(k) == want, str(a.get(k)))
    nested = [k for k, *_ in blocks(body) or []]
    banned = [k for k in ("password", "password_wo", "password_wo_version", "snapshot_identifier", "replicate_source_db",
                          "master_user_secret_kms_key_id", "restore_to_point_in_time", "s3_import") if k in a or k in nested]
    check(f"db instance {name}: no password attribute, snapshot, replica, restore or custom secret key", not banned, str(banned))
pgs = res("aws_db_parameter_group")
check("a db parameter group forces TLS (rds.force_ssl = 1, apply_method pending-reboot)", any(
    attrs(b2).get("name") == '"rds.force_ssl"' and attrs(b2).get("value") == '"1"'
    and attrs(b2).get("apply_method") == '"pending-reboot"'
    for _, b in pgs for k, _l, b2, *_ in blocks(b) or [] if k == "parameter"))
ALLOWED_DB = {"aws_db_instance", "aws_db_subnet_group", "aws_db_parameter_group"}
odd = [f"{k} {l[0]}" for k, l, *_ in allb if k in ("resource", "data") and l and l[0].startswith(("aws_db_", "aws_rds_"))
       and (k == "data" or l[0] not in ALLOWED_DB)]
check("only aws_db_instance, aws_db_subnet_group and aws_db_parameter_group among aws_db_* and aws_rds_*", not odd, str(odd))
dv = lambda n: v.get(n, "")
check("variable db_deletion_protection defaults to true", attrs(dv("db_deletion_protection")).get("default") == "true")
check("variable db_backup_retention_days defaults to 1 (the value the Free plan accepted) and has a validation",
      attrs(dv("db_backup_retention_days")).get("default") == "1" and any(k == "validation" for k, *_ in blocks(dv("db_backup_retention_days")) or []))
check("variable db_final_snapshot_name defaults to cropcms-final and has a validation",
      attrs(dv("db_final_snapshot_name")).get("default") == '"cropcms-final"' and any(k == "validation" for k, *_ in blocks(dv("db_final_snapshot_name")) or []))
alltf = "\n".join(tf.values())
leak = [w for w, rx in (("random_password", r"random_password"), ("secret_string", r"secret_string"),
                        ("aws_secretsmanager_secret_version", r"aws_secretsmanager_secret_version"),
                        ("provisioner", r"\bprovisioner\b|local-exec"), ("ephemeral block", r"(?m)^[ \t]*ephemeral[ \t]+\"")) if re.search(rx, alltf)]
check("no generated password, secret read, ephemeral block or provisioner in any .tf", not leak, str(leak))
for k, l, b, *_ in allb:
    if k != "output":
        continue
    val = attrs(b).get("value", "")
    ok = not re.search(r"(?i)passw|secret_string", val) and (
        "master_user_secret" not in val or val == "aws_db_instance.cms.master_user_secret[0].secret_arn")
    check(f"output {l[0]}: value does not expose a password or secret string", ok, val)

# --- budget ---
budgets = res("aws_budgets_budget")
check("exactly one budget is defined", len(budgets) == 1)
for name, body in budgets:
    a = attrs(body)
    check(f"budget {name}: limit is var.monthly_budget_usd, USD, MONTHLY, type COST",
          a.get("limit_amount") == "tostring(var.monthly_budget_usd)" and a.get("limit_unit") == '"USD"'
          and a.get("time_unit") == '"MONTHLY"' and a.get("budget_type") == '"COST"')
    check(f"budget {name}: credits excluded", attrs(next((b for k, _, b, *_ in blocks(body) or [] if k == "cost_types"), "")).get("include_credit") == "false")
    notes = sorted(
        (n.get("notification_type"), n.get("threshold"), n.get("threshold_type"), n.get("comparison_operator"), n.get("subscriber_email_addresses"))
        for n in (attrs(b) for k, _, b, *_ in blocks(body) or [] if k == "notification"))
    want = sorted([
        ('"ACTUAL"', "80", '"PERCENTAGE"', '"GREATER_THAN"', "[var.budget_email]"),
        ('"FORECASTED"', "100", '"PERCENTAGE"', '"GREATER_THAN"', "[var.budget_email]"),
    ])
    check(f"budget {name}: alerts on actual spend over 80 percent and forecast over 100 percent, to var.budget_email", notes == want, str(notes))
mb = v.get("monthly_budget_usd", "")
check("variable monthly_budget_usd defaults to 25", attrs(mb).get("default") == "25")

# --- bootstrap script ---
bs = (infra / "bootstrap-state.sh").read_text()
check("bootstrap: no ACL flag and no public or grant canned access", not re.search(r"--acl|--grant-|public-read|authenticated-read", bs))
pab = re.findall(r"put-public-access-block.*?(?:\n\n|\Z)", bs, re.S)
need = ("BlockPublicAcls=true", "IgnorePublicAcls=true", "BlockPublicPolicy=true", "RestrictPublicBuckets=true")
check("bootstrap: blocks all four kinds of public access", bool(pab) and all(n in pab[0] for n in need) and "=false" not in pab[0])
check("bootstrap: turns versioning on", "put-bucket-versioning" in bs and "Status=Enabled" in bs)
check("bootstrap: sets default encryption", "put-bucket-encryption" in bs and "SSEAlgorithm" in bs)

regions = re.findall(r'^\s*region\s*=\s*"([^"]+)"', tf.get("versions.tf", ""), re.M)
bs_region = re.findall(r"^REGION=(\S+)", bs, re.M)
check("versions.tf (backend and provider) and bootstrap-state.sh name the same region",
      len(regions) == 2 and len(set(regions + bs_region)) == 1 and len(bs_region) == 1, f"{regions} {bs_region}")

# --- secrets and identifiers ---
CANONICAL = "099720109477"  # public AMI publisher, not an account of ours
DOC_IP = re.compile(r"^(0\.0\.0\.0|10\..*|127\.0\.0\.1|6\.6\.6\.6|192\.0\.2\..*|198\.51\.100\..*|203\.0\.113\..*)$")
# Catches PASSWORD 'x', E'x', U&'x', = 'x', $$x$$ and a line break after the keyword, in any case.
# A psql variable (PASSWORD :'name') and prose pass.
SQL_PW = r"(?i)\bpassword\s*=?\s*(?:e|u&)?(?:'[^']{4,}'|\$(?:[a-z_]\w*)?\$)"
patterns = {
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "access key id": r"\b(AKIA|ASIA)[0-9A-Z]{16}\b",
    "quoted secret value": r"""(?i)(secret|passw(or)?d|token|api_?key)(_\w+)?\s*[=:]\s*["'][^"'$\s]{8,}["']""",
    "SQL password literal": SQL_PW,
}
AWS_HOST = (r"(?i)\b(?:[a-z0-9-]+\.[a-z0-9]{8,}\.[a-z]{2}(?:-[a-z]+)+-\d\.rds\.amazonaws\.com"
            r"|ec2-\d{1,3}(?:-\d{1,3}){3}\.[a-z0-9.-]*amazonaws\.com(?:\.cn)?"
            r"|[a-z0-9-]+\.[a-z]{2}(?:-[a-z]+)+-\d\.elb\.amazonaws\.com)\b")
EMAIL = r"(?<![\w.+-])[\w.+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w-])"
EMAIL_OK = re.compile(r"(?i)(^|\.)(example\.(com|org|net)|[^.]+\.example|example|test|invalid|localhost)$")
# Commented-out assignments count: an old value pasted into a comment is still a leak. The name must
# END in a secret word, so ACCESS_TOKEN_EXPIRE_MINUTES (a number) and SECRETARY are not secrets.
# `:` is taken only after an upper-case name, so prose such as "# Secret: ..." passes. The match is
# anchored per line with no adjacent overlapping quantifiers, so it stays linear.
ENV_ASSIGN = re.compile(r"[#\s]*(?:export\s+)?([A-Za-z0-9_]+)\s*([=:])(.*)")
ENV_SECRET_WORDS = {"PASSWORD", "SECRET", "KEY", "TOKEN", "PRIVATEKEY"}
ENV_URL_PW = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:([^@\s/]+)@")
ENV_NAME = re.compile(r"(^|/)\.env(\.|$)")


def blank_value(v):
    v = re.sub(r"(^|\s)#.*$", "", v).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    return v == "" or re.fullmatch(r"\$\{[^}]*\}", v) is not None


def env_secrets(text):
    """(line, what) for each non-blank secret value; never the value itself."""
    out = []
    for n, ln in enumerate(text.splitlines(), 1):
        m = ENV_ASSIGN.match(ln)
        if m:
            name, sep, val = m.groups()
            if name.upper().split("_")[-1] in ENV_SECRET_WORDS and (sep == "=" or name.isupper()) and not blank_value(val):
                out.append((n, name))
        for u in ENV_URL_PW.finditer(ln):
            if not blank_value(u.group(1)) and not u.group(1).startswith(("<", "$")):
                out.append((n, "a URL with credentials"))
    return out


def scan(name, text):
    out = []
    for label, rx in patterns.items():
        if re.search(rx, text):
            out.append(f"{name}: {label}")
    for m in re.finditer(r"ssh-(?:ed25519|rsa) AAAA[0-9A-Za-z+/=]+", text):
        if "EXAMPLE" not in m.group(0):
            out.append(f"{name}: a real-looking SSH public key")
    if not name.endswith(".terraform.lock.hcl"):
        out += [f"{name}: 12-digit id {i}" for i in re.findall(r"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])", text) if i != CANONICAL]
        out += [f"{name}: IPv4 {i}" for i in re.findall(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])", text)
                if all(int(o) < 256 for o in i.split(".")) and not DOC_IP.match(i)]
    out += [f"{name}: AWS hostname {m.group(0)}" for m in re.finditer(AWS_HOST, text)]
    out += [f"{name}: e-mail address outside example domains" for m in re.finditer(EMAIL, text) if not EMAIL_OK.search(m.group(1))]
    if ENV_NAME.search(name):
        out += [f"{name}: env-secret value set for {what} at line {n}" for n, what in env_secrets(text)]
    return out


def git_run(root, *a):
    try:
        r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
    except OSError:
        return 127, ""
    return r.returncode, r.stdout


def scan_set(root, use_git):
    """Files the scan reads. Never globs .env: that is the owner's real file."""
    paths = [p for p in sorted((root / "infra").rglob("*")) if p.is_file() and ".terraform" not in p.parts and
             not re.search(r"\.tfstate|\.tfvars(\.json)?$|^tfplan$|\.tfplan$", p.name)]
    paths += sorted(root.glob("docs/*.md"))
    paths += [root / n for n in ("README.md", "docker-compose.prod.yaml", "proxy/Caddyfile")]
    names = None
    if use_git:
        # Untracked but not ignored counts too, so a new template is scanned before `git add`.
        rc, out = git_run(root, "ls-files", "--cached", "--others", "--exclude-standard")
        if rc == 0:
            names = [n for n in out.splitlines() if ENV_NAME.search(n)]
    if names is None:
        names = [".env.example", ".env.prod.example"]
    paths += [root / n for n in names]
    return [p for p in dict.fromkeys(paths) if p.is_file()]


def scan_tree(root, use_git):
    return [b for p in scan_set(root, use_git) for b in scan(p.relative_to(root).as_posix(), p.read_text(errors="replace"))]


in_git = git_run(repo, "rev-parse", "--git-dir")[0] == 0
rels = {p.relative_to(repo).as_posix() for p in scan_set(repo, in_git)}
want_set = {"docs/operations.md", "README.md", "docker-compose.prod.yaml", "proxy/Caddyfile", ".env.example"}
check("the scan set includes the operations doc, README, prod compose, Caddyfile and .env.example", want_set <= rels, str(want_set - rels))
bad = scan_tree(repo, in_git)
check("no private key, access key id, quoted secret, SQL password literal, account id, real IP, AWS hostname, "
      "outside e-mail or env secret value in infra/, docs, README, prod compose, Caddyfile or tracked .env files", not bad, "; ".join(bad))

# Fixtures are assembled here, so this file carries no literal the rule would flag.
kw = "PASS" + "WORD"
lit = "hunter2" + "hunter2"
flagged = [f"CREATE ROLE a LOGIN {kw} '{lit}'", f"create role a login {kw.lower()} '{lit}'", f"{kw} E'abcdefg'",
           f"{kw} $$abcdefg$$", f"{kw.lower()} $tag$abcdefg$tag$", f"{kw} = 'abcdefgh'",
           f"ALTER ROLE cms_app {kw} '<new " + "pw>'", f"{kw}\n'abcdefgh'", f"{kw} U&'abcdefg'"]
clean = [f"CREATE ROLE cms_app LOGIN {kw} :'app_pw';", f"ALTER ROLE cms_app {kw} :'new_pw'",
         "Reset the role's " + kw.lower() + " with ALTER ROLE and a psql variable.", f"{kw} $1",
         "manage_master_user_" + kw.lower() + " = true", kw.lower() + "_wo = var.x", "DB_" + kw + "=", "PG" + kw + " is read from the environment"]
check("SQL password rule flags all 9 literal forms", all(re.search(SQL_PW, s) for s in flagged),
      str([s for s in flagged if not re.search(SQL_PW, s)]))
check("SQL password rule passes all 8 safe forms", not any(re.search(SQL_PW, s) for s in clean),
      str([s for s in clean if re.search(SQL_PW, s)]))
shipped = [infra / "README.md", infra / "db-bootstrap.sql", repo / "docs" / "operations.md", repo / ".env.example"]
check("shipped docs and SQL pass the SQL password rule",
      all(not re.search(SQL_PW, p.read_text()) for p in shipped if p.is_file()),
      str([p.name for p in shipped if p.is_file() and re.search(SQL_PW, p.read_text())]))

# Scan fixtures: a temporary tree laid out like the repo goes through the same file-set and scan code.
import tempfile
sv = "abc123" + "xyz"
dbpw = "DB_" + kw
bad_ip = ".".join(["172", "31", "4", "5"])
bad_tree = {
    "docs/a.md": "host " + bad_ip + "\n",
    "docker-compose.prod.yaml": "h: db." + "abcdefghijkl" + ".eu-north-1.rds." + "amazonaws.com\n",
    "proxy/Caddyfile": "tls x@" + "gmail" + ".com\n",
    ".env.prod.example": f"SECRET_KEY={sv}\n# {dbpw}={sv}\n",
    "README.md": "id " + "AK" + "IA" + "ABCDEFGHIJKLMNOP\n",
    "docs/other.md": "owner@" + "corp" + ".io\n",
}
good_tree = {
    "docs/a.md": "127.0.0.1 and 6.6.6.6 and checkip.amazonaws.com and me@example.com\n",
    "docker-compose.prod.yaml": "u: rds.amazonaws.com\n",
    "proxy/Caddyfile": "x\n",
    ".env.prod.example": f"SECRET_KEY=\nDB_USER=cms_app\n# {dbpw}=\n",
    "README.md": "x\n",
}


def layout(tree):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "infra").mkdir()
    for rel, body in tree.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    return d


import shutil
d = layout(bad_tree)
found = scan_tree(d, False)
shutil.rmtree(d)
want = [("docs/a.md", "IPv4 " + bad_ip), ("docker-compose.prod.yaml", "AWS hostname"), ("proxy/Caddyfile", "e-mail address"),
        (".env.prod.example", "env-secret value set for SECRET_KEY at line 1"), (".env.prod.example", f"env-secret value set for {dbpw} at line 2"),
        ("README.md", "access key id"), ("docs/other.md", "e-mail address")]
miss = [w for w in want if not any(f.startswith(w[0] + ":") and w[1] in f for f in found)]
check("scan fixtures: every new rule flags its bad file, new docs files included", not miss, str(miss))
check("scan fixtures: env-secret messages never carry the value", not any(sv in f for f in found))
d = layout(good_tree)
found = scan_tree(d, False)
shutil.rmtree(d)
check("scan fixtures: loopback, 6.6.6.6, service hostnames, example e-mail and blank env values pass", not found, str(found))

import os
cases_bad = [f"export {dbpw}=abc", f"## {dbpw}=abc", f"# # {dbpw}=abc", "# KEY=abc", "   KEY=abc", "AUTH_TOKEN=abc", "ACCESS_TOKEN=abc",
             "TOKEN=abc", f"{dbpw}:abc", "PRIVATEKEY=abc", "SECRET_KEY=abc", "OPENAI_API_KEY=abc", "DATABASE_URL=postgresql://u:pw@h/db",
             f"{dbpw}=a # note", f"{dbpw.lower()}=abc"]
cases_ok = ["SECRETARY=x", f"{dbpw}=\"\"", f"{dbpw}=''", f"{dbpw}= # note", f"{dbpw}=${{{dbpw}}}", f"{dbpw}=\"${{{dbpw}}}\"",
            "# ACCESS_TOKEN_EXPIRE_MINUTES=30", "ACCESS_TOKEN_EXPIRE_MINUTES=30", "DATABASE_URL=postgresql://u:${X}@h/db",
            "DATABASE_URL=postgresql://u:<password>@h/db", "DATABASE_URL=postgresql://u@h/db", "# Secret: do not commit", "SECRET_KEY=",
            "DB_USER=cms_app", "# KEY=", "SECRET_KEY= # set me"]
hit = lambda s: any("env-secret" in f for f in scan(".env.t", s + "\n"))
check("env-secret rule flags every secret form", all(hit(s) for s in cases_bad), str([s for s in cases_bad if not hit(s)]))
check("env-secret rule passes blank values, placeholders, non-secret names and prose", not any(hit(s) for s in cases_ok),
      str([s for s in cases_ok if hit(s)]))
import time
t0 = time.time()
scan(".env.t", " \t" * 40000 + "x\n" + "#" * 40000 + "\n")
check("env-secret rule is linear on long whitespace and # runs", time.time() - t0 < 2, f"{time.time() - t0:.1f}s")
d = layout({"infra/tfplan": bad_ip + "\n", "infra/x.tfplan": bad_ip + "\n", "infra/y.tf": bad_ip + "\n"})
found = scan_tree(d, False)
shutil.rmtree(d)
check("a local tfplan is not scanned, other infra files are", len(found) == 1 and found[0].startswith("infra/y.tf:"), str(found))
if git_run(repo, "--version")[0] == 0:
    d = layout({".gitignore": ".env.*\n!.env.example\n!.env.prod.example\n", ".env.prod.example": f"SECRET_KEY={sv}\n", ".env.prod": f"SECRET_KEY={sv}\n"})
    git_run(d, "init", "-q")
    found = scan_tree(d, True)
    shutil.rmtree(d)
    check("an untracked template is scanned, an ignored .env.prod is not", len(found) == 1 and found[0].startswith(".env.prod.example:"), str(found))
old_path = os.environ["PATH"]
os.environ["PATH"] = ""
rc = git_run(repo, "--version")[0]
os.environ["PATH"] = old_path
check("a missing git binary reads as not a checkout, without a crash", rc != 0)

# --- git ---
def git(*a):
    return git_run(repo, *a)
if in_git:
    for f in ("infra/terraform.tfvars", "infra/prod.auto.tfvars", "infra/terraform.tfvars.json", "infra/x.auto.tfvars.json", "infra/terraform.tfstate", "infra/terraform.tfstate.backup", "infra/.terraform/x", "infra/tfplan", "infra/x.tfplan"):
        check(f"git ignores {f}", git("check-ignore", "-q", f)[0] == 0)
    for f in ("infra/.terraform.lock.hcl", "infra/terraform.tfvars.example"):
        check(f"git does not ignore {f}", git("check-ignore", "-q", f)[0] == 1)
    tracked = git("ls-files")[1].splitlines()
    sens = [f for f in tracked if re.search(r"\.tfstate|\.tfvars(\.json)?$|\.pem$|(^|/)id_(rsa|ed25519|ecdsa)$|\.pfx$", f)]
    check("git tracks no state, tfvars or private key file", not sens, str(sens))
    # --no-index: a tracked file is otherwise reported as not ignored whatever the rules say.
    for f in (".env", ".env.prod", ".env.local", ".env.bak"):
        check(f"git ignores {f}", git("check-ignore", "-q", "--no-index", f)[0] == 0)
    for f in (".env.example", ".env.prod.example"):
        check(f"git does not ignore {f}", git("check-ignore", "-q", "--no-index", f)[0] == 1)
    envs = [f for f in tracked if ENV_NAME.search(f) and f not in (".env.example", ".env.prod.example")]
    check("git tracks no .env file besides the two examples", not envs, str(envs))
else:
    print("skip git checks (not a git checkout)")
di = repo / ".dockerignore"
dlines = di.read_text().splitlines() if di.is_file() else []
for rule in ("**/.env", "**/.env.*"):
    check(f".dockerignore has {rule} on its own line", rule in dlines)
last = max((dlines.index(r) for r in ("**/.env", "**/.env.*") if r in dlines), default=0)
check(".dockerignore has no ! negation after the **/.env lines", not any(l.startswith("!") for l in dlines[last:]))
check("the provider lock file exists", (infra / ".terraform.lock.hcl").is_file())

print("static checks:", "ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
PY

echo "-- bootstrap-state.sh with a stub aws"
bash ./bootstrap-state.test.sh || fails=$((fails + 1))

if [ "$static_only" -eq 0 ]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  # Same uid as the caller, so files Terraform writes into $tmp can be removed by the trap.
  dk() { docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e CHECKPOINT_DISABLE=1 "$@"; }

  echo "-- shellcheck"
  if dk -v "$PWD:/mnt:ro" "$SHELLCHECK_IMAGE" -x -s bash \
      bootstrap-state.sh bootstrap-state.test.sh db-bootstrap.test.sh check.sh user_data.sh; then
    echo "ok   shellcheck is clean"
  else
    echo "FAIL shellcheck"; fails=$((fails + 1))
  fi

  echo "-- terraform ($TF_IMAGE)"
  if dk -v "$PWD:/w:ro" -w /w "$TF_IMAGE" fmt -check -recursive -diff; then
    echo "ok   terraform fmt -check -recursive"
  else
    echo "FAIL terraform fmt"; fails=$((fails + 1))
  fi
  # A copy, so init's .terraform directory never lands in the checkout.
  mkdir "$tmp/cfg"
  cp ./*.tf user_data.sh .terraform.lock.hcl "$tmp/cfg/"
  # The provider download can fail on a network blip; a lock-file mismatch fails all three tries.
  init_rc=1
  for attempt in 1 2 3; do
    if dk -v "$tmp/cfg:/w" -w /w "$TF_IMAGE" init -backend=false -input=false -lockfile=readonly >"$tmp/init.log" 2>&1; then
      init_rc=0; break
    fi
    echo "terraform init attempt $attempt failed; retrying"; sleep 5
  done
  if [ "$init_rc" -eq 0 ] && dk -v "$tmp/cfg:/w" -w /w "$TF_IMAGE" validate >"$tmp/validate.log" 2>&1; then
    echo "ok   terraform init -backend=false (lock file honoured) and validate"
  else
    echo "FAIL terraform init/validate"; cat "$tmp/init.log" "$tmp/validate.log" 2>/dev/null; fails=$((fails + 1))
  fi

  # variables.tf alone needs no provider, so `plan` evaluates the real validation rules offline.
  mkdir "$tmp/vars"
  cp variables.tf "$tmp/vars/"
  cat >"$tmp/vars/probe.tf" <<'TF'
output "probe" { value = [var.ssh_cidr, var.ssh_public_key, var.budget_email, var.instance_type, var.db_backup_retention_days, var.db_deletion_protection, var.db_final_snapshot_name] }
TF
  cat >"$tmp/vars/cases.txt" <<'CASES'
ssh_cidr|203.0.113.7/32|accept
ssh_cidr|0.0.0.0/0|reject
ssh_cidr|10.0.0.0/8|reject
ssh_cidr|203.0.113.7|reject
ssh_cidr|garbage|reject
ssh_cidr||reject
ssh_cidr|203.0.113.7/31|reject
ssh_cidr|203.0.113.0/24|reject
ssh_cidr|203.0.113.7/0|reject
ssh_cidr|0.0.0.0/32|reject
ssh_cidr|256.0.113.7/32|reject
ssh_cidr|203.0.113.07/32|reject
ssh_cidr| 203.0.113.7/32|reject
ssh_cidr|::/0|reject
ssh_cidr|2001:db8::1/128|reject
ssh_public_key|ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE me@laptop|accept
ssh_public_key|ssh-rsa AAAAB3NzaC1yc2EAAAADAQABEXAMPLE me@laptop|reject
budget_email|me@example.com|accept
budget_email|not-an-address|reject
instance_type|t4g.small|accept
instance_type|t3.micro|reject
db_backup_retention_days|0|reject
db_backup_retention_days|36|reject
db_backup_retention_days|1|accept
db_backup_retention_days|7|accept
db_backup_retention_days|35|accept
db_backup_retention_days|1.5|reject
db_deletion_protection|true|accept
db_deletion_protection|false|accept
db_deletion_protection|maybe|reject
db_final_snapshot_name|cropcms-final|accept
db_final_snapshot_name|1cropcms|reject
db_final_snapshot_name|cropcms-|reject
db_final_snapshot_name|crop--cms|reject
db_final_snapshot_name||reject
db_final_snapshot_name|Crop-Cms-9|accept
CASES
  # Split so this file does not itself contain a private-key header for the secret scan to find.
  printf 'ssh_public_key|%s|reject\n' "-----BEGIN OPENSSH ""PRIVATE KEY-----" >>"$tmp/vars/cases.txt"
  cat >"$tmp/vars/run.sh" <<'SH'
ok_key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE me@laptop'
bad=0
while IFS='|' read -r name value want; do
  [ -n "$name" ] || continue
  set -- -var "ssh_cidr=203.0.113.7/32" -var "ssh_public_key=$ok_key" -var "budget_email=me@example.com"
  out=$(terraform plan -input=false -no-color "$@" -var "$name=$value" 2>&1); rc=$?
  if [ "$want" = accept ]; then
    [ "$rc" -eq 0 ] && r=ok || r=FAIL
  else
    [ "$rc" -ne 0 ] && echo "$out" | grep -Eq "Invalid value for (input )?variable" && echo "$out" | grep -q "variable \"$name\"" && r=ok || r=FAIL
  fi
  [ "$r" = ok ] || bad=$((bad + 1))
  echo "$r $name = '$value' must be ${want}ed"
done </w/cases.txt
exit "$bad"
SH
  echo "-- variable validation (terraform plan on variables.tf alone)"
  if dk -v "$tmp/vars:/w" -w /w --entrypoint sh "$TF_IMAGE" /w/run.sh 2>&1 | sed 's/^ok /ok   /'; [ "${PIPESTATUS[0]}" -eq 0 ]; then
    echo "ok   every variable rule accepts and rejects as expected"
  else
    echo "FAIL variable validation"; fails=$((fails + 1))
  fi

  # The repo's .dockerignore against fake env files at the root and one level down, through the
  # same COPY the Dockerfile does; FROM scratch keeps it free of any image pull.
  echo "-- .dockerignore keeps env files out of the build context"
  mkdir -p "$tmp/di/ctx/sub" "$tmp/di/out"
  cp ../.dockerignore "$tmp/di/ctx/.dockerignore"
  for f in .env .env.prod .env.example .env.prod.example sub/.env sub/.env.prod keep sub/keep; do : >"$tmp/di/ctx/$f"; done
  printf 'FROM scratch\nCOPY . /ctx\n' >"$tmp/di/Dockerfile"
  if docker build -q -f "$tmp/di/Dockerfile" --output "type=local,dest=$tmp/di/out" "$tmp/di/ctx" >"$tmp/di/build.log" 2>&1 &&
     [ "$(cd "$tmp/di/out/ctx" && find . -type f | sort | tr '\n' ' ')" = "./.dockerignore ./keep ./sub/keep " ]; then
    echo "ok   only the non-env files reach the build context, nested ones included"
  else
    echo "FAIL .dockerignore probe: $(cd "$tmp/di/out/ctx" 2>/dev/null && find . -type f | sort | tr '\n' ' ')"
    cat "$tmp/di/build.log"; fails=$((fails + 1))
  fi

  echo "-- db-bootstrap.sql in a throwaway database (pulls images and pip packages on a first run)"
  bash ./db-bootstrap.test.sh || fails=$((fails + 1))
fi

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails check group(s) FAILED"; exit 1; fi
