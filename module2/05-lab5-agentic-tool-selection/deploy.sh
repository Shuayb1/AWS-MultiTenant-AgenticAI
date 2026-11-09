#!/bin/bash
# Deploy the Agent Lambda to AWS
# Usage: source /workshop/static/code/module1/setup_env.sh && bash deploy.sh
#
# This script packages the agent Lambda with its dependencies and deploys it.
# The Lambda function and API Gateway /agent route are pre-provisioned by the
# CloudFormation stack — this script only updates the function code.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/deploy"
MODULE2_DIR="$SCRIPT_DIR/.."

# Resolve function name from CFN stack
STACK_NAME="${STACK_NAME:-workshop-infrastructure}"
AGENT_FUNCTION_NAME="${AGENT_FUNCTION_NAME:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentFunctionName'].OutputValue" \
  --output text 2>/dev/null || echo "${STACK_NAME}-agent")}"

echo "Building agent package..."
rm -rf "$DEPLOY_DIR" && mkdir -p "$DEPLOY_DIR"

# Install dependencies (targeting Lambda's Amazon Linux x86_64 platform).
# --python-version MUST match the AgentFunction runtime in the CFN template
# (python3.13). Building for the wrong version ships a pydantic_core native
# extension (pydantic_core._pydantic_core) the runtime can't import, which
# surfaces as "Internal server error" from API Gateway.
python3 -m pip install strands-agents mcp numpy --quiet --target "$DEPLOY_DIR" \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.13 --only-binary=:all:

# Copy the agent Lambda handler
cp "$SCRIPT_DIR/agent.py" "$DEPLOY_DIR/index.py"

# Copy the tool modules from Labs 2, 3, and 4
cp "$MODULE2_DIR/02-lab2-function-calling-tool/tool_postgresql.py" "$DEPLOY_DIR/"
cp "$MODULE2_DIR/03-lab3-knowledge-base-tool/tool_opensearch.py" "$DEPLOY_DIR/"
cp "$MODULE2_DIR/04-lab4-mcp-server-tool/tool_s3_vectors.py" "$DEPLOY_DIR/"

# Package
echo "Creating deployment package..."
cd "$DEPLOY_DIR" && zip -r ../agent.zip . > /dev/null && cd "$SCRIPT_DIR"
rm -rf "$DEPLOY_DIR"

echo "Deploying to Lambda: $AGENT_FUNCTION_NAME"

# Check zip size — if > 50MB, upload via S3
ZIP_SIZE=$(stat -f%z "$SCRIPT_DIR/agent.zip" 2>/dev/null || stat -c%s "$SCRIPT_DIR/agent.zip" 2>/dev/null)
if [ "$ZIP_SIZE" -gt 50000000 ]; then
  # Upload to S3 first (Lambda direct upload limit is 50MB zipped)
  S3_BUCKET="${S3_VECTOR_BUCKET:-$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" \
    --output text 2>/dev/null)}"
  S3_KEY="lambda-deploy/agent.zip"
  echo "  Zip is ${ZIP_SIZE} bytes — uploading via S3..."
  aws s3 cp "$SCRIPT_DIR/agent.zip" "s3://$S3_BUCKET/$S3_KEY" --quiet
  aws lambda update-function-code \
    --function-name "$AGENT_FUNCTION_NAME" \
    --s3-bucket "$S3_BUCKET" \
    --s3-key "$S3_KEY" \
    --output text --query 'FunctionName'
else
  aws lambda update-function-code \
    --function-name "$AGENT_FUNCTION_NAME" \
    --zip-file fileb://agent.zip \
    --output text --query 'FunctionName'
fi

aws lambda wait function-updated --function-name "$AGENT_FUNCTION_NAME"

# Set KNOWLEDGE_BASE_ID if available (created by Lab 3 setup_knowledge_base.py)
KB_ENV_FILE="$MODULE2_DIR/03-lab3-knowledge-base-tool/.kb_env"
if [ -f "$KB_ENV_FILE" ]; then
  source "$KB_ENV_FILE"
  if [ -n "$KNOWLEDGE_BASE_ID" ]; then
    aws lambda update-function-configuration \
      --function-name "$AGENT_FUNCTION_NAME" \
      --environment "Variables={STACK_NAME=${STACK_NAME},AWS_REGION_NAME=${AWS_REGION:-us-east-1},AURORA_CLUSTER_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='AuroraClusterArn'].OutputValue" --output text),APP_USER_SECRET_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='AppUserSecretArn'].OutputValue" --output text),S3_VECTOR_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" --output text),KNOWLEDGE_BASE_ID=${KNOWLEDGE_BASE_ID}}" \
      --output text --query 'FunctionName' > /dev/null
    aws lambda wait function-updated --function-name "$AGENT_FUNCTION_NAME"
    echo "  KNOWLEDGE_BASE_ID=$KNOWLEDGE_BASE_ID (from Lab 3)"
  fi
fi

rm -f "$SCRIPT_DIR/agent.zip"

echo ""
echo "Done. Agent Lambda is live."
echo "Test with:"
echo "  curl -s -X POST \$API_GATEWAY_URL/agent \\"
echo "    -H \"Authorization: Bearer \$TOKEN\" \\"
echo "    -H \"Content-Type: application/json\" \\"
echo "    -d '{\"query\": \"What is the condition of the roof?\"}'"
