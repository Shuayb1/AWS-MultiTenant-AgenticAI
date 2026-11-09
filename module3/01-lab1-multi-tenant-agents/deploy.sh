#!/usr/bin/env bash
# deploy.sh — Deploy the pooled multi-tenant agent to AgentCore Runtime
# Module 3, Lab 1
#
# This script:
#   1. Packages the agent code into a zip deployment artifact
#   2. Uploads to S3 (required by AgentCore Runtime)
#   3. Creates or updates the AgentCore Runtime agent
#   4. Updates the backend Lambda with the Agent Runtime ARN
#   5. Outputs the API Gateway URL for testing
#
# Prerequisites:
#   - source setup_env.sh (sets COGNITO_USER_POOL_ID, AGENTCORE_TVM_ROLE_ARN, etc.)
#   - pip install -r requirements.txt
#
# Usage:
#   bash deploy.sh
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME=${STACK_NAME:-workshop-infrastructure}
AGENT_NAME="workshopModule3PooledAgent"
S3_BUCKET="${S3_BUCKET_NAME:-saas-data-${ACCOUNT_ID}-${REGION}}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying Pooled Multi-Tenant Agent                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Get Cognito details for JWT authorizer (Phase 1: JWT inbound auth)
COGNITO_USER_POOL_ID=${COGNITO_USER_POOL_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "")}

COGNITO_CLIENT_ID=${COGNITO_CLIENT_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "")}

COGNITO_DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}/.well-known/openid-configuration"

# ---------------------------------------------------------------------------
# Step 1: Package the agent code
# ---------------------------------------------------------------------------
echo "→ Step 1: Packaging agent code..."

DEPLOY_DIR=$(mktemp -d)
cp agent.py "$DEPLOY_DIR/"
cp requirements.txt "$DEPLOY_DIR/"

# Ensure uv is available (needed for cross-platform ARM64 packaging)
if ! command -v uv &> /dev/null; then
  echo "  Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
  export PATH="$HOME/.local/bin:$PATH"
fi

# Install dependencies for ARM64 (AgentCore Runtime runs on ARM64/Graviton)
echo "  Installing dependencies for ARM64 target..."
if ! uv pip install \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.13 \
  --target "$DEPLOY_DIR" \
  -r requirements.txt --quiet 2>/dev/null; then
  echo "  ERROR: Failed to install ARM64 dependencies."
  echo "  Ensure 'uv' is installed and requirements.txt has ARM64-compatible packages."
  rm -rf "$DEPLOY_DIR"
  exit 1
fi

# Create deployment zip
DEPLOY_ZIP="/tmp/agentcore-module3-lab1.zip"
rm -f "$DEPLOY_ZIP"
(cd "$DEPLOY_DIR" && zip -r "$DEPLOY_ZIP" . --quiet)
rm -rf "$DEPLOY_DIR"

echo "  Package created: $DEPLOY_ZIP ($(du -h "$DEPLOY_ZIP" | cut -f1))"

# ---------------------------------------------------------------------------
# Step 2: Upload to S3
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 2: Uploading to S3..."

# Upload to existing S3 bucket (from CFN stack)
S3_KEY="agentcore/${AGENT_NAME}/deployment_package.zip"
aws s3 cp "$DEPLOY_ZIP" "s3://${S3_BUCKET}/${S3_KEY}" --quiet

echo "  Uploaded: s3://${S3_BUCKET}/${S3_KEY}"

# ---------------------------------------------------------------------------
# Step 3: Create or update AgentCore Runtime
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 3: Creating/updating AgentCore Runtime..."

# Get or use the execution role (pre-created by CFN stack)
RUNTIME_ROLE_NAME="${STACK_NAME}-agentcore-memory-exec-role"
RUNTIME_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${RUNTIME_ROLE_NAME}"

# Verify the role exists
if ! aws iam get-role --role-name "$RUNTIME_ROLE_NAME" --no-cli-pager > /dev/null 2>&1; then
  echo "  ERROR: Runtime execution role not found: $RUNTIME_ROLE_NAME"
  echo "  This role should be pre-created by the workshop-infrastructure CFN stack."
  exit 1
fi

# Check if runtime already exists — delete it for a clean deployment
EXISTING_RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "None")

if [ "$EXISTING_RUNTIME_ID" != "None" ] && [ -n "$EXISTING_RUNTIME_ID" ] && [ "$EXISTING_RUNTIME_ID" != "null" ]; then
  echo "  Deleting existing runtime: $EXISTING_RUNTIME_ID"
  aws bedrock-agentcore-control delete-agent-runtime \
    --agent-runtime-id "$EXISTING_RUNTIME_ID" \
    --no-cli-pager > /dev/null 2>&1 || true
  # Wait for deletion to complete
  echo "  Waiting for deletion..."
  WAIT=0
  while [ $WAIT -lt 120 ]; do
    STATUS=$(aws bedrock-agentcore-control get-agent-runtime \
      --agent-runtime-id "$EXISTING_RUNTIME_ID" \
      --query "status" --output text --no-cli-pager 2>/dev/null || echo "DELETED")
    if [ "$STATUS" = "DELETED" ] || [ "$STATUS" = "None" ]; then
      echo "  Deleted ✓"
      break
    fi
    printf "  Status: %s (waiting...)\\r" "$STATUS"
    sleep 5
    WAIT=$((WAIT + 5))
  done
fi

# Always create fresh
if true; then
  echo "  Creating new AgentCore Runtime: $AGENT_NAME (JWT auth)"

  if ! RESPONSE=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "$AGENT_NAME" \
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
    --authorizer-configuration "{
      \"customJWTAuthorizer\": {
        \"discoveryUrl\": \"${COGNITO_DISCOVERY_URL}\",
        \"allowedAudience\": [\"${COGNITO_CLIENT_ID}\"]
      }
    }" \
    --request-header-configuration '{"requestHeaderAllowlist": ["Authorization", "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization"]}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout": 300, "maxLifetime": 1800}' \
    --no-cli-pager 2>&1); then
    echo ""
    echo "  ERROR: Failed to create AgentCore Runtime."
    echo "  Response: $RESPONSE"
    echo ""
    echo "  Common causes:"
    echo "    - Missing iam:PassRole permission on your role for: $RUNTIME_ROLE_ARN"
    echo "    - Invalid Cognito configuration (Pool ID: $COGNITO_USER_POOL_ID)"
    echo "    - AgentCore service not available in region: $REGION"
    exit 1
  fi

  AGENT_RUNTIME_ARN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agentRuntimeArn',''))" 2>/dev/null || echo "")
fi

if [ -z "$AGENT_RUNTIME_ARN" ]; then
  echo ""
  echo "  ERROR: Could not determine Agent Runtime ARN from response."
  echo "  Response: $RESPONSE"
  exit 1
fi

echo "  Agent Runtime ARN: $AGENT_RUNTIME_ARN"

# Construct the invocation endpoint URL
RUNTIME_ID=$(echo "$AGENT_RUNTIME_ARN" | sed 's|.*runtime/||')
export AGENT_ENDPOINT="https://bedrock-agentcore.${REGION}.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3A${REGION}%3A${ACCOUNT_ID}%3Aruntime%2F${RUNTIME_ID}/invocations?qualifier=DEFAULT"

# ---------------------------------------------------------------------------
# Step 4: Wait for runtime to become ACTIVE
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 4: Waiting for runtime to become ACTIVE..."

MAX_WAIT=120
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --query "status" --output text --no-cli-pager 2>/dev/null || echo "UNKNOWN")

  if [ "$STATUS" = "ACTIVE" ] || [ "$STATUS" = "READY" ]; then
    echo "  Runtime is $STATUS ✓"
    break
  elif [ "$STATUS" = "FAILED" ]; then
    echo "  ERROR: Runtime entered FAILED state."
    aws bedrock-agentcore-control get-agent-runtime \
      --agent-runtime-id "$RUNTIME_ID" \
      --query "failureReason" --output text --no-cli-pager 2>/dev/null
    exit 1
  fi

  printf "  Status: %s (waiting...)\\r" "$STATUS"
  sleep 5
  ELAPSED=$((ELAPSED + 5))
done

if [ "$STATUS" != "ACTIVE" ] && [ "$STATUS" != "READY" ]; then
  echo "  WARNING: Runtime not READY after ${MAX_WAIT}s (status: $STATUS). It may still be provisioning."
fi

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Agent Deployed (Pooled Multi-Tenant - JWT Auth)           ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Agent Runtime ARN: $AGENT_RUNTIME_ARN"
echo "║  Inbound Auth:      JWT (Cognito)                            ║"
echo "║  Authorizer:        $COGNITO_DISCOVERY_URL"
echo "║  Agent Endpoint:    $AGENT_ENDPOINT"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Save endpoint to env file so it persists across shell sessions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cat > "$SCRIPT_DIR/.agent_env" <<EOF
export AGENT_ENDPOINT="$AGENT_ENDPOINT"
export AGENT_RUNTIME_ARN="$AGENT_RUNTIME_ARN"
export RUNTIME_ID="$RUNTIME_ID"
EOF

echo "Run:  source .agent_env"
echo ""
