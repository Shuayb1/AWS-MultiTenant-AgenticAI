#!/usr/bin/env bash
# deploy_interceptor.sh — Deploy the Gateway interceptor Lambda
# Module 3, Lab 2
#
# The interceptor is invoked by the Gateway on every MCP request.
# It extracts tenantId from the JWT, injects X-Tenant-ID header
# (for Lambda targets), and logs an audit trail.
#
# Prerequisites:
#   - source setup_env.sh
#   - interceptor.py patched with Task 2.4 solutions
#
# Usage:
#   bash deploy_interceptor.sh
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
FUNCTION_NAME="workshop-gateway-interceptor"

echo "→ Deploying interceptor: $FUNCTION_NAME"

# Package (include PyJWT dependency)
DEPLOY_DIR=$(mktemp -d)
cp interceptor.py "$DEPLOY_DIR/"
pip install PyJWT -t "$DEPLOY_DIR" --quiet 2>/dev/null

DEPLOY_ZIP="/tmp/interceptor.zip"
rm -f "$DEPLOY_ZIP"
(cd "$DEPLOY_DIR" && zip -r "$DEPLOY_ZIP" . --quiet)
rm -rf "$DEPLOY_DIR"

# Check if function exists
if aws lambda get-function --function-name "$FUNCTION_NAME" --no-cli-pager > /dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file fileb://"$DEPLOY_ZIP" \
    --no-cli-pager > /dev/null
  aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
else
  # Create execution role
  ROLE_NAME="${FUNCTION_NAME}-role"
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --no-cli-pager > /dev/null 2>&1 || true

  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
    --no-cli-pager 2>/dev/null || true

  sleep 10

  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.13 \
    --handler interceptor.lambda_handler \
    --role "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}" \
    --zip-file fileb://"$DEPLOY_ZIP" \
    --timeout 10 \
    --no-cli-pager > /dev/null

  aws lambda wait function-active-v2 --function-name "$FUNCTION_NAME"
fi

echo "  Interceptor deployed: $FUNCTION_NAME"
echo "  Responsibilities:"
echo "    - Extract tenantId from JWT"
echo "    - Inject X-Tenant-ID header (Lambda targets)"
echo "    - Audit log (all targets)"
