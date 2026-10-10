#!/bin/bash
set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}"
export AWS_REGION="${AWS_REGION:-us-east-2}"
export AWS_DEFAULT_REGION="$AWS_REGION"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
EXPECTED_ACCOUNT="${EXPECTED_ACCOUNT:?set EXPECTED_ACCOUNT to the AWS account id you mean to deploy to}"
if [[ "$ACCOUNT" != "$EXPECTED_ACCOUNT" ]]; then
  echo "refusing to deploy: account is $ACCOUNT" >&2
  exit 1
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TABLE=f1-whiteboard
INGEST=f1-whiteboard-ingest
READ=f1-whiteboard-read
API_NAME=f1-whiteboard
SCHEDULE=f1-whiteboard-weekly

aws dynamodb create-table \
  --table-name "$TABLE" \
  --attribute-definitions AttributeName=season,AttributeType=S AttributeName=item,AttributeType=S \
  --key-schema AttributeName=season,KeyType=HASH AttributeName=item,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST

aws dynamodb wait table-exists --table-name "$TABLE"

cat > /tmp/f1-lambda-trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF

INGEST_ROLE_ARN=$(aws iam create-role --role-name f1-whiteboard-ingest-role --assume-role-policy-document file:///tmp/f1-lambda-trust.json --query Role.Arn --output text)
READ_ROLE_ARN=$(aws iam create-role --role-name f1-whiteboard-read-role --assume-role-policy-document file:///tmp/f1-lambda-trust.json --query Role.Arn --output text)
aws iam attach-role-policy --role-name f1-whiteboard-ingest-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam attach-role-policy --role-name f1-whiteboard-read-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

cat > /tmp/f1-ddb-policy.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:TransactWriteItems"],"Resource":"arn:aws:dynamodb:us-east-2:${ACCOUNT}:table/${TABLE}"}]}
EOF
aws iam put-role-policy --role-name f1-whiteboard-ingest-role --policy-name write-season --policy-document file:///tmp/f1-ddb-policy.json
aws iam put-role-policy --role-name f1-whiteboard-read-role --policy-name read-season --policy-document file:///tmp/f1-ddb-policy.json

sleep 10
ZIP=/tmp/f1-whiteboard-lambda.zip
rm -f "$ZIP"
(cd "$ROOT" && zip -qr "$ZIP" f1board)

aws lambda create-function \
  --function-name "$INGEST" \
  --runtime python3.12 \
  --handler f1board.ingest_handler.handler \
  --role "$INGEST_ROLE_ARN" \
  --timeout 60 \
  --memory-size 256 \
  --zip-file "fileb://$ZIP" \
  --environment "Variables={TABLE_NAME=$TABLE,SEASON=2026,JOLPICA_BASE=https://api.jolpi.ca/ergast/f1/2026/}"

aws lambda create-function \
  --function-name "$READ" \
  --runtime python3.12 \
  --handler f1board.read_handler.handler \
  --role "$READ_ROLE_ARN" \
  --timeout 15 \
  --memory-size 256 \
  --zip-file "fileb://$ZIP" \
  --environment "Variables={TABLE_NAME=$TABLE,SEASON=2026,ALLOWED_ORIGIN=*}"

aws lambda wait function-active --function-name "$INGEST"
aws lambda invoke --function-name "$INGEST" --cli-binary-format raw-in-base64-out --payload '{}' /tmp/f1-ingest-out.json
python3 -c 'import json; body=json.load(open("/tmp/f1-ingest-out.json")); assert body["ok"] is True; print(body["updatedAt"])'

API_ID=$(aws apigatewayv2 create-api --name "$API_NAME" --protocol-type HTTP --query ApiId --output text)
READ_ARN=$(aws lambda get-function --function-name "$READ" --query Configuration.FunctionArn --output text)
INTEGRATION_ID=$(aws apigatewayv2 create-integration --api-id "$API_ID" --integration-type AWS_PROXY --integration-uri "$READ_ARN" --payload-format-version 2.0 --query IntegrationId --output text)
aws apigatewayv2 create-route --api-id "$API_ID" --route-key "GET /season" --target "integrations/$INTEGRATION_ID"
aws apigatewayv2 create-stage --api-id "$API_ID" --stage-name '$default' --auto-deploy
aws apigatewayv2 update-api --api-id "$API_ID" --cors-configuration AllowOrigins='*',AllowMethods='GET,OPTIONS',AllowHeaders='*'
aws lambda add-permission --function-name "$READ" --statement-id apigw-season --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:us-east-2:${ACCOUNT}:${API_ID}/*/*/season"
SEASON_URL="https://${API_ID}.execute-api.us-east-2.amazonaws.com/season"

cat > /tmp/f1-scheduler-trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
SCHED_ROLE_ARN=$(aws iam create-role --role-name f1-whiteboard-schedule-role --assume-role-policy-document file:///tmp/f1-scheduler-trust.json --query Role.Arn --output text)
INGEST_ARN=$(aws lambda get-function --function-name "$INGEST" --query Configuration.FunctionArn --output text)
cat > /tmp/f1-sched-policy.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"lambda:InvokeFunction","Resource":"${INGEST_ARN}"}]}
EOF
aws iam put-role-policy --role-name f1-whiteboard-schedule-role --policy-name invoke-ingest --policy-document file:///tmp/f1-sched-policy.json
sleep 10
cat > /tmp/f1-schedule-target.json <<EOF
{"Arn":"${INGEST_ARN}","RoleArn":"${SCHED_ROLE_ARN}"}
EOF
aws scheduler create-schedule \
  --name "$SCHEDULE" \
  --schedule-expression "cron(0 9 ? * MON *)" \
  --schedule-expression-timezone "America/New_York" \
  --flexible-time-window Mode=OFF \
  --target file:///tmp/f1-schedule-target.json

APP_ID=$(aws amplify create-app --name f1-whiteboard --platform WEB --query app.appId --output text)
aws amplify create-branch --app-id "$APP_ID" --branch-name main --stage PRODUCTION
SITE=$(mktemp -d)
cp "$ROOT"/web/* "$SITE"/
# Start from web/config.js so the ask and sign-in settings survive; override only
# what this run knows. Set ASK_API, COGNITO_DOMAIN, COGNITO_CLIENT_ID to change them.
SEASON_API="$SEASON_URL" python3 - "$SITE/config.js" <<'PY'
import os
import re
import sys

path = sys.argv[1]
text = open(path).read()
for key in ("SEASON_API", "ASK_API", "COGNITO_DOMAIN", "COGNITO_CLIENT_ID"):
    value = os.environ.get(key)
    if value:
        text = re.sub(rf"(?m)^window\.{key} = .*$", lambda _m: f'window.{key} = "{value}";', text)
open(path, "w").write(text)
PY
if grep -q 'example.invalid' "$SITE/config.js"; then
  echo "warning: config.js still has placeholder values; ask and sign-in will not work until ASK_API, COGNITO_DOMAIN and COGNITO_CLIENT_ID are set" >&2
fi
(cd "$SITE" && zip -qr /tmp/f1-whiteboard-site.zip .)
aws amplify create-deployment --app-id "$APP_ID" --branch-name main --output json > /tmp/f1-amplify-deploy.json
JOB_ID=$(python3 -c 'import json; print(json.load(open("/tmp/f1-amplify-deploy.json"))["jobId"])')
UPLOAD_URL=$(python3 -c 'import json; print(json.load(open("/tmp/f1-amplify-deploy.json"))["zipUploadUrl"])')
curl --fail --upload-file /tmp/f1-whiteboard-site.zip "$UPLOAD_URL"
aws amplify start-deployment --app-id "$APP_ID" --branch-name main --job-id "$JOB_ID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  STATUS=$(aws amplify get-job --app-id "$APP_ID" --branch-name main --job-id "$JOB_ID" --query job.summary.status --output text)
  echo "amplify=$STATUS"
  [[ "$STATUS" == "SUCCEED" ]] && break
  [[ "$STATUS" == "FAILED" || "$STATUS" == "CANCELLED" ]] && exit 1
  sleep 3
done
aws lambda update-function-configuration \
  --function-name "$READ" \
  --environment "Variables={TABLE_NAME=$TABLE,SEASON=2026,ALLOWED_ORIGIN=https://main.${APP_ID}.amplifyapp.com}"
aws lambda wait function-updated --function-name "$READ"
echo "SITE=https://main.${APP_ID}.amplifyapp.com"
echo "API=$SEASON_URL"
