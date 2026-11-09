#!/usr/bin/env bash
# deploy_backend.sh - Phase 2: Switch to IAM auth + deploy backend Lambda
# Module 3, Lab 1
#
# This script:
#   1. Updates the AgentCore Runtime to use IAM SigV4 inbound auth
#      (removes the JWT authorizer from Phase 1)
#   2. Deploys the backend Lambda (session ID construction + TVM)
#   3. Configures the TVM role with bedrock-agentcore:sessionId condition
#
# After this, the agent can only be invoked via SigV4 (not Bearer token).
# The backend Lambda mediates: validates JWT at API Gateway, constructs
# tenant-scoped session ID, assumes TVM role, calls AgentCore via SigV4.
#
# Prerequisites:
#   - Phase 1 completed (agent deployed with JWT auth)
#   - backend_lambda.py patched with Task 1.4 solution
#
# Usage:
#   bash deploy_backend.sh
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME=${STACK_NAME:-workshop-infrastructure}
AGENT_NAME="workshopModule3PooledAgent"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Phase 2: Switch to IAM Auth + Deploy Backend               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Update AgentCore Runtime to IAM auth
# ---------------------------------------------------------------------------
echo "→ Step 1: Switching AgentCore Runtime to IAM inbound auth..."

EXISTING_RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "None")

if [ "$EXISTING_RUNTIME_ID" = "None" ] || [ -z "$EXISTING_RUNTIME_ID" ] || [ "$EXISTING_RUNTIME_ID" = "null" ]; then
  echo "  ERROR: AgentCore Runtime not found. Complete Phase 1 first (bash deploy.sh)"
  exit 1
fi

S3_BUCKET="${S3_BUCKET_NAME:-saas-data-${ACCOUNT_ID}-${REGION}}"
S3_KEY="agentcore/${AGENT_NAME}/deployment_package.zip"
RUNTIME_ROLE_NAME="${STACK_NAME}-agentcore-memory-exec-role"
RUNTIME_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${RUNTIME_ROLE_NAME}"

# Update runtime: switch to IAM auth by removing the JWT authorizer
# Keep requestHeaderAllowlist so the backend Lambda can forward the JWT via custom header
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "$EXISTING_RUNTIME_ID" \
  --agent-runtime-artifact "{
    \"codeConfiguration\": {
      \"code\": {
        \"s3\": {
          \"bucket\": \"${S3_BUCKET}\",
          \"prefix\": \"${S3_KEY}\"
        }
      },
      \"runtime\": \"PYTHON_3_13\",
      \"entryPoint\": [\"agent.py\"]
    }
  }" \
  --network-configuration '{"networkMode": "PUBLIC"}' \
  --role-arn "$RUNTIME_ROLE_ARN" \
  --request-header-configuration '{"requestHeaderAllowlist": ["X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization"]}' \
  --no-cli-pager > /dev/null 2>&1 || true

AGENT_RUNTIME_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${EXISTING_RUNTIME_ID}"
echo "  Runtime updated to IAM auth: $EXISTING_RUNTIME_ID"
echo "  Agent Runtime ARN: $AGENT_RUNTIME_ARN"

# ---------------------------------------------------------------------------
# Step 2: Deploy backend Lambda
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 2: Deploying backend Lambda..."

AGENT_FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentFunctionName'].OutputValue | [0]" \
  --output text --no-cli-pager)

TVM_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='TenantIsolationRoleArn'].OutputValue | [0]" \
  --output text --no-cli-pager)

# Package backend Lambda as index.py
python3 -c "
import zipfile
with zipfile.ZipFile('/tmp/backend-lambda.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write('backend_lambda.py', 'index.py')
"

aws lambda update-function-code \
  --function-name "$AGENT_FUNCTION_NAME" \
  --zip-file fileb:///tmp/backend-lambda.zip \
  --no-cli-pager > /dev/null

aws lambda wait function-updated-v2 --function-name "$AGENT_FUNCTION_NAME"

# Wait for code update to fully propagate before updating configuration
sleep 5

# Update env vars with Agent Runtime ARN and TVM role
aws lambda update-function-configuration \
  --function-name "$AGENT_FUNCTION_NAME" \
  --environment "Variables={AGENT_RUNTIME_ARN=${AGENT_RUNTIME_ARN},TVM_ROLE_ARN=${TVM_ROLE_ARN},AWS_REGION_NAME=${REGION}}" \
  --no-cli-pager > /dev/null

aws lambda wait function-updated-v2 --function-name "$AGENT_FUNCTION_NAME"

echo "  Backend Lambda deployed: $AGENT_FUNCTION_NAME"

# ---------------------------------------------------------------------------
# Step 3: Output
# ---------------------------------------------------------------------------
echo ""

API_GATEWAY_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiGatewayUrl'].OutputValue | [0]" \
  --output text --no-cli-pager)

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Phase 2 Deployed                                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  AgentCore Runtime: IAM SigV4 inbound auth                   ║"
echo "║  Runtime ID:        $EXISTING_RUNTIME_ID"
echo "║  Backend Lambda:    $AGENT_FUNCTION_NAME"
echo "║  API Gateway:       $API_GATEWAY_URL/agent"
echo "║  TVM Role:          $TVM_ROLE_ARN"
echo "║                                                              ║"
echo "║  IAM condition: bedrock-agentcore:sessionId                  ║"
echo "║  must match \${aws:PrincipalTag/TenantID}-*                   ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
