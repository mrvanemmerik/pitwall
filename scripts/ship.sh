#!/usr/bin/env bash
# Build and deploy Pit Wall to your AWS account.
#
#   scripts/ship.sh ask|ingest|read|site|all [--dry-run] [--skip-tests]
#
# Lambdas are rebuilt from the repo plus the pinned dependencies in requirements/ (built for Lambda
# (Linux x86_64, Python 3.12)). The site keeps the LIVE config.js (the repo's has placeholders).
# Settings come from the environment or scripts/ship.local.env (git-ignored; see ship.local.env.example):
#   AWS_PROFILE, AWS_REGION (default us-east-2), PITWALL_APP_ID (Amplify app), PITWALL_SITE_URL
# --dry-run builds everything and prints sizes but changes nothing in AWS.
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f scripts/ship.local.env ] && . scripts/ship.local.env
REGION="${AWS_REGION:-us-east-2}"
APP_ID="${PITWALL_APP_ID:-}"
SITE_URL="${PITWALL_SITE_URL:-}"

TARGET="${1:-}"
shift || true
DRY=0
TESTS=1
for flag in "$@"; do
  case "$flag" in
    --dry-run) DRY=1 ;;
    --skip-tests) TESTS=0 ;;
    *) echo "unknown flag: $flag" >&2; exit 2 ;;
  esac
done
case "$TARGET" in ask|ingest|read|site|all) ;; *) sed -n 2,12p "$0"; exit 2 ;; esac
if [ "$DRY" = 0 ] && { [ "$TARGET" = site ] || [ "$TARGET" = all ]; }; then
  [ -n "$APP_ID" ] && [ -n "$SITE_URL" ] || { echo "set PITWALL_APP_ID and PITWALL_SITE_URL (see scripts/ship.local.env.example)" >&2; exit 2; }
fi

aws() { command aws --region "$REGION" "$@"; }
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

if [ "$TESTS" = 1 ]; then
  python3 -m pytest -q
  node --test web/*.test.js >/dev/null
  echo "tests passed"
fi

package() { # name, then pip requirements
  local name="$1"; shift
  local dir="$BUILD/$name"
  mkdir -p "$dir/f1board"
  if [ "$#" -gt 0 ]; then
    python3 -m pip install --quiet --target "$dir" --platform manylinux2014_x86_64 \
      --implementation cp --python-version 3.12 --only-binary=:all: "$@" 2>&1 | grep -E "^ERROR: (Could not|No matching|Failed|Cannot)" && { echo "dependency install failed" >&2; exit 1; } || true
  fi
  cp f1board/*.py "$dir/f1board/"
  (cd "$dir" && find . -name __pycache__ -prune -exec rm -rf {} + && rm -rf bin && zip -q -r "$BUILD/$name.zip" . -x '*.DS_Store')
  echo "built $name: $(du -h "$BUILD/$name.zip" | cut -f1)"
}

deploy_lambda() { # function name, zip name
  [ "$DRY" = 1 ] && { echo "dry run: would update $1"; return; }
  aws lambda update-function-code --function-name "$1" --zip-file "fileb://$BUILD/$2.zip" --query LastUpdateStatus --output text >/dev/null
  aws lambda wait function-updated --function-name "$1"
  echo "deployed $1"
}

ship_site() {
  local dir="$BUILD/site"
  mkdir -p "$dir"
  cp web/index.html web/styles.css web/render.js web/ask.js web/app.js "$dir/"
  [ -n "$SITE_URL" ] || { echo "set PITWALL_SITE_URL to build the site" >&2; exit 2; }
  curl -fsS "$SITE_URL/config.js" -o "$dir/config.js"
  if grep -q "example.invalid" "$dir/config.js"; then echo "live config.js looks like the placeholder; refusing" >&2; exit 1; fi
  (cd "$dir" && zip -q -r "$BUILD/site.zip" .)
  echo "built site: $(du -h "$BUILD/site.zip" | cut -f1)"
  [ "$DRY" = 1 ] && { echo "dry run: would deploy the site"; return; }
  local out job url status
  out="$(aws amplify create-deployment --app-id "$APP_ID" --branch-name main --output json)"
  job="$(printf '%s' "$out" | python3 -c 'import sys,json; print(json.load(sys.stdin)["jobId"])')"
  url="$(printf '%s' "$out" | python3 -c 'import sys,json; print(json.load(sys.stdin)["zipUploadUrl"])')"
  curl -fsS -o /dev/null -T "$BUILD/site.zip" "$url" -H "Content-Type: application/zip"
  aws amplify start-deployment --app-id "$APP_ID" --branch-name main --job-id "$job" >/dev/null
  for _ in $(seq 1 30); do
    status="$(aws amplify get-job --app-id "$APP_ID" --branch-name main --job-id "$job" --query job.summary.status --output text)"
    [ "$status" = SUCCEED ] || [ "$status" = FAILED ] && break
    sleep 4
  done
  echo "site deployment $job: $status"
  [ "$status" = SUCCEED ]
}

case "$TARGET" in ask|all) package ask -r requirements/ask.txt; deploy_lambda f1-whiteboard-ask ask ;; esac
case "$TARGET" in ingest|all) package ingest -r requirements/ingest.txt; deploy_lambda f1-whiteboard-ingest ingest ;; esac
case "$TARGET" in read|all) package read; deploy_lambda f1-whiteboard-read read ;; esac
case "$TARGET" in site|all) ship_site ;; esac
