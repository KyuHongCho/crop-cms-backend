#!/usr/bin/env bash
# Creates the S3 bucket that holds Terraform state. Terraform cannot create the bucket that stores
# its own state, so this runs once with the AWS CLI. Safe to run again; --dry-run changes nothing.
set -euo pipefail

REGION=eu-north-1
DRY_RUN=0
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

# Prints each command; runs it unless this is a dry run.
run() {
  printf '+'; printf ' %q' "$@"; printf '\n'
  if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

if [ "$DRY_RUN" -eq 1 ]; then
  echo "(dry run: no AWS call is made)"
  run aws sts get-caller-identity --query Account --output text
  ACCOUNT_ID=ACCOUNT_ID
else
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  case "$ACCOUNT_ID" in
    *[!0-9]* | "") echo "unexpected account id from aws sts: '$ACCOUNT_ID'" >&2; exit 1 ;;
  esac
fi
BUCKET="cropcms-tfstate-$ACCOUNT_ID"

if [ "$DRY_RUN" -eq 0 ] && aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "bucket $BUCKET already exists; re-applying its settings"
else
  run aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION"
fi

run aws s3api put-bucket-versioning --bucket "$BUCKET" --region "$REGION" \
  --versioning-configuration Status=Enabled
run aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
run aws s3api put-bucket-encryption --bucket "$BUCKET" --region "$REGION" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

if [ "$DRY_RUN" -eq 1 ]; then
  printf '\n(dry run: nothing was created; run without --dry-run to do it)\n'
else
  cat <<MSG

State bucket ready. Initialise Terraform with:
  terraform init -backend-config="bucket=$BUCKET"
MSG
fi
