#!/usr/bin/env bash
# deploy.sh — Deploy the DynamoDB tenant repository Lambda
# Module 1, Lab 3
#
# Packages dynamodb_tenant_repository.py with dependencies and deploys
# to the workshop-infrastructure-dynamodb-documents Lambda.
#
# Prerequisites:
#   - source setup_env.sh (sets STACK_NAME, AWS_REGION)
#   - Lab 2 completed (token_vending_machine.py)
#   - Tasks 3.1-3.4 completed in dynamodb_tenant_repository.py
#
# Usage:
#   bash deploy.sh
set -euo pipefail

STACK_NAME=${STACK_NAME:-workshop-infrastructure}

echo "→ Deploying DynamoDB tenant repository..."

# Package
DEPLOY_DIR=$(mktemp -d)
cp dynamodb_tenant_repository.py "$DEPLOY_DIR/index.py"
cp ../02-lab2-token-vending-machine/token_vending_machine.py "$DEPLOY_DIR/"
pip install cachetools -t "$DEPLOY_DIR" --quiet 2>/dev/null

DEPLOY_ZIP="/tmp/dynamodb-deploy.zip"
rm -f "$DEPLOY_ZIP"
(cd "$DEPLOY_DIR" && zip -r "$DEPLOY_ZIP" . --quiet)
rm -rf "$DEPLOY_DIR"

echo "  Package created: $(du -h "$DEPLOY_ZIP" | cut -f1)"

# Deploy
FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='DocumentsFunctionName'].OutputValue | [0]" \
  --output text --no-cli-pager)

aws lambda update-function-code \
  --function-name "$FUNCTION_NAME" \
  --zip-file fileb://"$DEPLOY_ZIP" \
  --no-cli-pager > /dev/null

aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
rm -f "$DEPLOY_ZIP"

echo "  Deployed: $FUNCTION_NAME"
echo ""
echo "Test with:"
echo "  curl -s -X POST \$API_GATEWAY_URL/documents \\"
echo "    -H \"Authorization: Bearer \$TOKEN\" \\"
echo "    -H \"Content-Type: application/json\" \\"
echo "    -d '{\"id\": \"doc-001\", \"title\": \"Test\", \"status\": \"draft\"}'"
