#!/usr/bin/env bash
# deploy.sh — Deploy the Aurora Data API client Lambda
# Module 1, Lab 4
#
# Packages aurora_data_api_client.py and deploys to the
# workshop-infrastructure-aurora-documents Lambda.
#
# Prerequisites:
#   - source setup_env.sh (sets STACK_NAME, AWS_REGION)
#   - Tasks 4.2-4.4 completed in aurora_data_api_client.py
#
# Usage:
#   bash deploy.sh
set -euo pipefail

STACK_NAME=${STACK_NAME:-workshop-infrastructure}

echo "→ Deploying Aurora Data API client..."

# Package (no extra dependencies needed - boto3 is in the Lambda runtime)
DEPLOY_ZIP="/tmp/aurora-deploy.zip"
rm -f "$DEPLOY_ZIP"
cp aurora_data_api_client.py index.py
zip "$DEPLOY_ZIP" index.py --quiet
rm -f index.py

echo "  Package created: $(du -h "$DEPLOY_ZIP" | cut -f1)"

# Deploy
FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AuroraFunctionName'].OutputValue | [0]" \
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
echo "  curl -s -X POST \$API_GATEWAY_URL/aurora/documents \\"
echo "    -H \"Authorization: Bearer \$TOKEN\" \\"
echo "    -H \"Content-Type: application/json\" \\"
echo "    -d '{\"id\": \"doc-001\", \"title\": \"Test\", \"content\": \"Hello\"}'"
