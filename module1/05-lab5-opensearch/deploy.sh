#!/usr/bin/env bash
# deploy.sh — Deploy the OpenSearch tenant client Lambda
# Module 1, Lab 5
#
# Packages opensearch_tenant_client.py with dependencies and deploys
# to the workshop-infrastructure-opensearch-documents Lambda.
#
# Prerequisites:
#   - source setup_env.sh (sets STACK_NAME, AWS_REGION)
#   - Lab 2 completed (token_vending_machine.py)
#   - Tasks 5.1-5.4 completed in opensearch_tenant_client.py
#
# Usage:
#   bash deploy.sh
set -euo pipefail

STACK_NAME=${STACK_NAME:-workshop-infrastructure}

echo "→ Deploying OpenSearch tenant client..."

# Package
DEPLOY_DIR=$(mktemp -d)
cp opensearch_tenant_client.py "$DEPLOY_DIR/index.py"
cp ../02-lab2-token-vending-machine/token_vending_machine.py "$DEPLOY_DIR/"
pip install opensearch-py requests-aws4auth cachetools -t "$DEPLOY_DIR" --quiet 2>/dev/null

DEPLOY_ZIP="/tmp/opensearch-deploy.zip"
rm -f "$DEPLOY_ZIP"
(cd "$DEPLOY_DIR" && zip -r "$DEPLOY_ZIP" . --quiet)
rm -rf "$DEPLOY_DIR"

echo "  Package created: $(du -h "$DEPLOY_ZIP" | cut -f1)"

# Deploy
FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='OpenSearchFunctionName'].OutputValue | [0]" \
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
echo "  curl -s -X POST \$API_GATEWAY_URL/opensearch/documents \\"
echo "    -H \"Authorization: Bearer \$TOKEN\" \\"
echo "    -H \"Content-Type: application/json\" \\"
echo "    -d '{\"id\": \"doc-001\", \"title\": \"Test\", \"content\": \"Hello\"}'"
