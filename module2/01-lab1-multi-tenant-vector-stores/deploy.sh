#!/usr/bin/env bash
# deploy.sh — Deploy the ingestion Lambda
# Module 2, Lab 1
#
# Packages ingest.py and updates the pre-created Lambda function.
# The function reads sample documents from S3, chunks and embeds them,
# and stores vectors in Aurora pgvector, OpenSearch, and S3 Vectors.
#
# Prerequisites:
#   - source setup_env.sh (sets STACK_NAME, AWS_REGION)
#   - python setup_vector_stores.py (creates vector stores)
#   - Tasks 1.1 and 1.2 completed in ingest.py
#
# Usage:
#   bash deploy.sh
set -euo pipefail

STACK_NAME=${STACK_NAME:-workshop-infrastructure}

echo "Packaging ingest Lambda..."

# Package
DEPLOY_DIR=$(mktemp -d)
cp ingest.py "$DEPLOY_DIR/index.py"

DEPLOY_ZIP="/tmp/ingest-deploy.zip"
rm -f "$DEPLOY_ZIP"
(cd "$DEPLOY_DIR" && zip -r "$DEPLOY_ZIP" . --quiet)
rm -rf "$DEPLOY_DIR"

echo "  Package created: $(du -h "$DEPLOY_ZIP" | cut -f1)"

# Get function name from CFN outputs
FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='IngestFunctionName'].OutputValue | [0]" \
  --output text --no-cli-pager)

if [ -z "$FUNCTION_NAME" ] || [ "$FUNCTION_NAME" = "None" ]; then
  FUNCTION_NAME="workshop-ingest"
fi

echo "Creating/updating function: $FUNCTION_NAME"

# Deploy code
aws lambda update-function-code \
  --function-name "$FUNCTION_NAME" \
  --zip-file fileb://"$DEPLOY_ZIP" \
  --no-cli-pager > /dev/null

aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
rm -f "$DEPLOY_ZIP"

# Print config
CONFIG=$(aws lambda get-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --query "{Runtime:Runtime,Timeout:Timeout,MemorySize:MemorySize,Env:Environment.Variables}" \
  --output json --no-cli-pager)

RUNTIME=$(echo "$CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['Runtime'])")
TIMEOUT=$(echo "$CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['Timeout'])")
MEMORY=$(echo "$CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['MemorySize'])")

echo "  Runtime: $RUNTIME"
echo "  Timeout: ${TIMEOUT}s"
echo "  Memory: ${MEMORY}MB"
echo "  Environment:"
echo "$CONFIG" | python3 -c "
import sys, json
env = json.load(sys.stdin).get('Env', {})
for k,v in sorted(env.items()):
    print(f'    {k}={v}')
"

FUNCTION_ARN=$(aws lambda get-function \
  --function-name "$FUNCTION_NAME" \
  --query "Configuration.FunctionArn" \
  --output text --no-cli-pager)

echo "Deploy complete: $FUNCTION_ARN"
