#!/usr/bin/env bash
# Runs bootstrap-state.sh against a stub `aws` that records its arguments, so the exact command
# sequence, the dry run and a second run can be asserted with no account and no network.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
export STUB_DIR="$tmp" STUB_LOG="$tmp/calls.log"
# Built at run time so no account-id-shaped literal sits in a tracked file.
export STUB_ACCOUNT=${STUB_ACCOUNT:-$(printf '%012d' 424242)}

cat >"$tmp/bin/aws" <<'STUB'
#!/usr/bin/env bash
echo "$*" >>"$STUB_LOG"
case "$1 $2" in
  "sts get-caller-identity") echo "$STUB_ACCOUNT" ;;
  "s3api head-bucket") [ -e "$STUB_DIR/bucket" ] ;;
  "s3api create-bucket") touch "$STUB_DIR/bucket" ;;
esac
STUB
chmod +x "$tmp/bin/aws"
export PATH="$tmp/bin:$PATH"

fails=0
check() { if [ "$2" = ok ]; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
same() { if diff "$2" "$3" >"$tmp/diff" 2>&1; then check "$1" ok; else check "$1" no; cat "$tmp/diff"; fi; }

B="cropcms-tfstate-$STUB_ACCOUNT"
R="--region eu-north-1"
settings=$(cat <<EOT
s3api put-bucket-versioning --bucket $B $R --versioning-configuration Status=Enabled
s3api put-public-access-block --bucket $B $R --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
s3api put-bucket-encryption --bucket $B $R --server-side-encryption-configuration {"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}
EOT
)
head_="sts get-caller-identity --query Account --output text
s3api head-bucket --bucket $B $R"
create="s3api create-bucket --bucket $B $R --create-bucket-configuration LocationConstraint=eu-north-1"

: >"$STUB_LOG"
rc=0; bash "$here/bootstrap-state.sh" >"$tmp/out1" 2>&1 || rc=$?; check "first run exits 0" "$([ "$rc" -eq 0 ] && echo ok)"
printf '%s\n%s\n%s\n' "$head_" "$create" "$settings" >"$tmp/want1"
same "first run: sts, head-bucket, create-bucket, versioning, public-access-block, encryption" "$tmp/want1" "$STUB_LOG"
check "first run prints the backend snippet" "$(grep -qF "terraform init -backend-config=\"bucket=$B\"" "$tmp/out1" && echo ok)"

: >"$STUB_LOG"
rc=0; bash "$here/bootstrap-state.sh" >"$tmp/out2" 2>&1 || rc=$?; check "second run exits 0" "$([ "$rc" -eq 0 ] && echo ok)"
printf '%s\n%s\n' "$head_" "$settings" >"$tmp/want2"
same "second run: no create-bucket, settings re-applied" "$tmp/want2" "$STUB_LOG"

rm "$tmp/bucket"; : >"$STUB_LOG"
rc=0; bash "$here/bootstrap-state.sh" --dry-run >"$tmp/out3" 2>&1 || rc=$?; check "dry run exits 0" "$([ "$rc" -eq 0 ] && echo ok)"
check "dry run makes no aws call" "$([ ! -s "$STUB_LOG" ] && echo ok)"
check "dry run did not create anything" "$([ ! -e "$tmp/bucket" ] && echo ok)"
check "dry run does not claim the bucket is ready" "$(! grep -qF 'State bucket ready' "$tmp/out3" && echo ok)"
for want in "create-bucket" "LocationConstraint=eu-north-1" "put-bucket-versioning" "put-public-access-block" \
  "put-bucket-encryption" "cropcms-tfstate-ACCOUNT_ID"; do
  check "dry run prints: $want" "$(grep -qF -- "$want" "$tmp/out3" && echo ok)"
done

rc=0; bash "$here/bootstrap-state.sh" --nope >/dev/null 2>&1 || rc=$?; check "unknown argument exits 2" "$([ "$rc" -eq 2 ] && echo ok)"

: >"$STUB_LOG"; rc=0; STUB_ACCOUNT=not-a-number bash "$here/bootstrap-state.sh" >/dev/null 2>&1 || rc=$?
check "a non-numeric account id stops the script after one call" "$([ "$rc" -eq 1 ] && [ "$(wc -l <"$STUB_LOG")" -eq 1 ] && echo ok)"

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
