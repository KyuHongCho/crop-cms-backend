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
check("ingress rules exist (the checks below are not vacuous)", len(rules) >= 4)
for name, body in rules:
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
      sum(1 for _, b in rules if attrs(b).get("from_port") == "22") == 1)
check("80/tcp, 443/tcp and 443/udp are all present", {(80, "tcp"), (443, "tcp"), (443, "udp")} <= seen)
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
DOC_IP = re.compile(r"^(0\.0\.0\.0|10\..*|192\.0\.2\..*|198\.51\.100\..*|203\.0\.113\..*)$")
patterns = {
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "access key id": r"\b(AKIA|ASIA)[0-9A-Z]{16}\b",
    "quoted secret value": r"""(?i)(secret|passw(or)?d|token|api_?key)(_\w+)?\s*[=:]\s*["'][^"'$\s]{8,}["']""",
}
files = [p for p in sorted(infra.rglob("*")) if p.is_file() and ".terraform" not in p.parts and
         not re.search(r"\.tfstate|\.tfvars(\.json)?$", p.name)]
bad = []
for p in files:
    t = p.read_text(errors="replace")
    for label, rx in patterns.items():
        if re.search(rx, t):
            bad.append(f"{p.name}: {label}")
    for m in re.finditer(r"ssh-(?:ed25519|rsa) AAAA[0-9A-Za-z+/=]+", t):
        if "EXAMPLE" not in m.group(0):
            bad.append(f"{p.name}: a real-looking SSH public key")
    if p.name != ".terraform.lock.hcl":
        bad += [f"{p.name}: 12-digit id {i}" for i in re.findall(r"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])", t) if i != CANONICAL]
        bad += [f"{p.name}: IPv4 {i}" for i in re.findall(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])", t)
                if all(int(o) < 256 for o in i.split(".")) and not DOC_IP.match(i)]
check("no private key, access key id, quoted secret, account id or real IP in infra/ files", not bad, "; ".join(bad))

# --- git ---
def git(*a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    return r.returncode, r.stdout
if git("rev-parse", "--git-dir")[0] == 0:
    for f in ("infra/terraform.tfvars", "infra/prod.auto.tfvars", "infra/terraform.tfvars.json", "infra/x.auto.tfvars.json", "infra/terraform.tfstate", "infra/terraform.tfstate.backup", "infra/.terraform/x", "infra/tfplan", "infra/x.tfplan"):
        check(f"git ignores {f}", git("check-ignore", "-q", f)[0] == 0)
    for f in ("infra/.terraform.lock.hcl", "infra/terraform.tfvars.example"):
        check(f"git does not ignore {f}", git("check-ignore", "-q", f)[0] == 1)
    tracked = git("ls-files")[1].splitlines()
    sens = [f for f in tracked if re.search(r"\.tfstate|\.tfvars(\.json)?$|\.pem$|(^|/)id_(rsa|ed25519|ecdsa)$|\.pfx$", f)]
    check("git tracks no state, tfvars or private key file", not sens, str(sens))
else:
    print("skip git checks (not a git checkout)")
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
      bootstrap-state.sh bootstrap-state.test.sh check.sh user_data.sh; then
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
output "probe" { value = [var.ssh_cidr, var.ssh_public_key, var.budget_email, var.instance_type] }
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
    [ "$rc" -ne 0 ] && echo "$out" | grep -q "Invalid value for variable" && echo "$out" | grep -q "variable \"$name\"" && r=ok || r=FAIL
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
fi

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails check group(s) FAILED"; exit 1; fi
