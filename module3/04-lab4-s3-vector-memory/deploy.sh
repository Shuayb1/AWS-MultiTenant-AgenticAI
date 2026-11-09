#!/usr/bin/env bash
# deploy.sh — Deploy Lab 4 Agent (S3 Vector Memory)
# Module 3, Lab 4
#
# Supports two deployment modes:
#   bash deploy.sh              — Direct code deployment (starter toolkit, faster)
#   bash deploy.sh --container  — Container deployment (Docker, guaranteed no init timeout)
#
# This script handles:
#   1. Creates S3 Vector bucket + per-tenant indexes
#   2. Creates/updates TVM IAM role
#   3. Deploys the agent (code or container mode)
#   4. Attaches permissions to execution role
#
# Prerequisites:
#   - source setup_env.sh
#   - agent.py patched (Tasks 4.1-4.3)
#
# Usage:
#   bash deploy.sh              # default: code deployment
#   bash deploy.sh --container  # fallback: container deployment
set -euo pipefail

DEPLOY_MODE="code"
if [[ "${1:-}" == "--container" ]]; then
  DEPLOY_MODE="container"
fi

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME="${STACK_NAME:-workshop-infrastructure}"
AGENT_NAME="workshopModule3Lab4S3VectorAgent"
TVM_ROLE_NAME="${STACK_NAME}-lab4-s3vectors-tvm-role"
VECTOR_BUCKET_NAME="workshop-vector-memory-${ACCOUNT_ID}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying Lab 4 Agent (S3 Vector Memory)                   ║"
echo "║  Mode: ${DEPLOY_MODE}                                       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Create S3 Vector bucket + indexes
# ---------------------------------------------------------------------------
echo "→ Step 1: Ensuring S3 Vector bucket and indexes exist..."

if aws s3vectors get-vector-bucket --vector-bucket-name "$VECTOR_BUCKET_NAME" --no-cli-pager &>/dev/null; then
  echo "  Bucket: $VECTOR_BUCKET_NAME (exists)"
else
  echo "  Creating bucket: $VECTOR_BUCKET_NAME"
  aws s3vectors create-vector-bucket --vector-bucket-name "$VECTOR_BUCKET_NAME" --no-cli-pager > /dev/null
  sleep 5
fi

for TENANT in tenant-001 tenant-002; do
  INDEX_NAME="memory-${TENANT}"
  if aws s3vectors get-index --vector-bucket-name "$VECTOR_BUCKET_NAME" --index-name "$INDEX_NAME" --no-cli-pager &>/dev/null 2>&1; then
    echo "  Index: $INDEX_NAME (exists)"
  else
    echo "  Creating index: $INDEX_NAME"
    aws s3vectors create-index \
      --vector-bucket-name "$VECTOR_BUCKET_NAME" \
      --index-name "$INDEX_NAME" \
      --data-type float32 --dimension 1024 --distance-metric cosine \
      --metadata-configuration '{"nonFilterableMetadataKeys":["content","stored_at"]}' \
      --no-cli-pager > /dev/null 2>&1 || true
  fi
done

# ---------------------------------------------------------------------------
# Step 2: Create or update TVM IAM role
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 2: Ensuring TVM role exists..."

TVM_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${TVM_ROLE_NAME}"
TRUST_POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"AWS\":\"arn:aws:iam::${ACCOUNT_ID}:root\"},\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"]},{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"bedrock-agentcore.amazonaws.com\"},\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"]}]}"

if aws iam get-role --role-name "$TVM_ROLE_NAME" --no-cli-pager &>/dev/null; then
  aws iam update-assume-role-policy --role-name "$TVM_ROLE_NAME" --policy-document "$TRUST_POLICY" --no-cli-pager
else
  aws iam create-role --role-name "$TVM_ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "TVM role for S3 Vector Memory tenant isolation" \
    --no-cli-pager > /dev/null
  sleep 10
fi

S3V_POLICY=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3vectors:QueryVectors","s3vectors:GetVectors","s3vectors:PutVectors","s3vectors:DeleteVectors","s3vectors:ListVectors","s3vectors:GetIndex","s3vectors:ListIndexes"],"Resource":"arn:aws:s3vectors:${REGION}:${ACCOUNT_ID}:bucket/${VECTOR_BUCKET_NAME}/index/memory-\${aws:PrincipalTag/TenantID}"},{"Effect":"Allow","Action":["s3vectors:GetVectorBucket","s3vectors:ListVectorBuckets"],"Resource":"arn:aws:s3vectors:${REGION}:${ACCOUNT_ID}:bucket/${VECTOR_BUCKET_NAME}"}]}
EOF
)
aws iam put-role-policy --role-name "$TVM_ROLE_NAME" \
  --policy-name "S3VectorsTenantPolicy" --policy-document "$S3V_POLICY" --no-cli-pager

echo "  TVM Role: $TVM_ROLE_ARN"

# ---------------------------------------------------------------------------
# Step 3: Resolve Cognito
# ---------------------------------------------------------------------------
COGNITO_USER_POOL_ID="${COGNITO_USER_POOL_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue | [0]" \
  --output text --no-cli-pager)}"
COGNITO_CLIENT_ID="${COGNITO_CLIENT_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue | [0]" \
  --output text --no-cli-pager)}"
COGNITO_DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}/.well-known/openid-configuration"

# ---------------------------------------------------------------------------
# Step 3: Deploy agent
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 3: Deploying agent (${DEPLOY_MODE} mode)..."

if [ "$DEPLOY_MODE" = "container" ]; then
  # =========================================================================
  # CONTAINER DEPLOYMENT (Docker → ECR → containerConfiguration)
  # =========================================================================
  BUILD_DIR=$(mktemp -d)
  cp "$SCRIPT_DIR/agent.py" "$BUILD_DIR/"
  cp "$SCRIPT_DIR/requirements-deploy.txt" "$BUILD_DIR/requirements.txt"

  cat > "$BUILD_DIR/Dockerfile" <<'DOCKERFILE'
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py .
EXPOSE 8080
CMD ["python", "agent.py"]
DOCKERFILE

  if command -v docker &> /dev/null; then
    CONTAINER_CMD="sudo docker"
    [[ "$(uname)" == "Darwin" ]] && CONTAINER_CMD="docker"
  elif command -v finch &> /dev/null; then
    CONTAINER_CMD="finch"
  else
    echo "  ERROR: No container runtime found. Use 'bash deploy.sh' (code mode) instead."
    rm -rf "$BUILD_DIR"
    exit 1
  fi

  ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/workshop-lab4-s3vector-agent"
  aws ecr describe-repositories --repository-names workshop-lab4-s3vector-agent --no-cli-pager &>/dev/null || \
    aws ecr create-repository --repository-name workshop-lab4-s3vector-agent --no-cli-pager > /dev/null

  aws ecr get-login-password --region "$REGION" | $CONTAINER_CMD login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" 2>/dev/null

  echo "  Building and pushing ARM64 image..."
  $CONTAINER_CMD buildx create --use --name arm64builder 2>/dev/null || true
  $CONTAINER_CMD buildx build --platform linux/arm64 -t "${ECR_REPO}:latest" --push "$BUILD_DIR"
  rm -rf "$BUILD_DIR"

  # Delete existing runtime
  EXISTING_RUNTIME=$(aws bedrock-agentcore-control list-agent-runtimes \
    --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" \
    --output text --no-cli-pager 2>/dev/null || echo "")
  if [ -n "$EXISTING_RUNTIME" ] && [ "$EXISTING_RUNTIME" != "None" ]; then
    echo "  Deleting old runtime: $EXISTING_RUNTIME"
    aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$EXISTING_RUNTIME" --no-cli-pager 2>/dev/null || true
    sleep 15
  fi

  # Create execution role
  EXEC_ROLE_NAME="workshop-lab4-agent-exec-role"
  EXEC_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${EXEC_ROLE_NAME}"
  if ! aws iam get-role --role-name "$EXEC_ROLE_NAME" --no-cli-pager &>/dev/null; then
    aws iam create-role --role-name "$EXEC_ROLE_NAME" \
      --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
      --no-cli-pager > /dev/null
    sleep 10
  fi
  aws iam put-role-policy --role-name "$EXEC_ROLE_NAME" --policy-name "Lab4AgentPolicy" \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"],\"Resource\":\"${TVM_ROLE_ARN}\"},{\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\",\"bedrock:Converse\",\"bedrock:ConverseStream\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"s3vectors:*\"],\"Resource\":[\"arn:aws:s3vectors:${REGION}:${ACCOUNT_ID}:bucket/${VECTOR_BUCKET_NAME}\",\"arn:aws:s3vectors:${REGION}:${ACCOUNT_ID}:bucket/${VECTOR_BUCKET_NAME}/*\"]},{\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"}]}" \
    --no-cli-pager
  aws iam put-role-policy --role-name "$EXEC_ROLE_NAME" --policy-name "ECRAccess" \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ecr:GetAuthorizationToken\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"ecr:BatchGetImage\",\"ecr:GetDownloadUrlForLayer\",\"ecr:BatchCheckLayerAvailability\"],\"Resource\":\"arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/workshop-lab4-*\"}]}" \
    --no-cli-pager

  # Create runtime
  echo "  Creating AgentCore Runtime (container)..."
  RESPONSE=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "$AGENT_NAME" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_REPO}:latest\"}}" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --role-arn "$EXEC_ROLE_ARN" \
    --authorizer-configuration "{\"customJWTAuthorizer\":{\"discoveryUrl\":\"${COGNITO_DISCOVERY_URL}\",\"allowedAudience\":[\"${COGNITO_CLIENT_ID}\"]}}" \
    --request-header-configuration '{"requestHeaderAllowlist":["Authorization"]}' \
    --environment-variables "{\"S3_VECTOR_BUCKET_NAME\":\"${VECTOR_BUCKET_NAME}\",\"S3_VECTOR_TVM_ROLE_ARN\":\"${TVM_ROLE_ARN}\",\"AWS_REGION\":\"${REGION}\"}" \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":300,"maxLifetime":1800}' \
    --no-cli-pager 2>&1)

  RUNTIME_ARN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agentRuntimeArn',''))" 2>/dev/null || echo "")
  RUNTIME_ID=$(echo "$RUNTIME_ARN" | sed 's|.*runtime/||')

else
  # =========================================================================
  # CODE DEPLOYMENT (starter toolkit)
  # =========================================================================
  if ! python3 -c "import bedrock_agentcore_starter_toolkit" 2>/dev/null; then
    pip install -q bedrock-agentcore-starter-toolkit
  fi

  if [ -f "$SCRIPT_DIR/requirements-deploy.txt" ]; then
    cp "$SCRIPT_DIR/requirements.txt" "$SCRIPT_DIR/requirements.txt.bak"
    cp "$SCRIPT_DIR/requirements-deploy.txt" "$SCRIPT_DIR/requirements.txt"
    RESTORE_REQS=1
  fi
  rm -f "$SCRIPT_DIR/.bedrock_agentcore.yaml"

  python3 - <<PYEOF
import urllib.parse
from pathlib import Path
from bedrock_agentcore_starter_toolkit.operations.runtime import (
    configure_bedrock_agentcore, launch_bedrock_agentcore,
)
SCRIPT_DIR = Path("$SCRIPT_DIR")
configure_bedrock_agentcore(
    agent_name="$AGENT_NAME",
    entrypoint_path=SCRIPT_DIR / "agent.py",
    source_path=str(SCRIPT_DIR),
    authorizer_configuration={"customJWTAuthorizer":{"discoveryUrl":"$COGNITO_DISCOVERY_URL","allowedAudience":["$COGNITO_CLIENT_ID"]}},
    request_header_configuration={"requestHeaderAllowlist":["Authorization"]},
    region="$REGION", non_interactive=True, runtime_type="PYTHON_3_13",
)
result = launch_bedrock_agentcore(
    config_path=SCRIPT_DIR / ".bedrock_agentcore.yaml",
    agent_name="$AGENT_NAME", use_codebuild=True, auto_update_on_conflict=True,
    env_vars={"S3_VECTOR_BUCKET_NAME":"$VECTOR_BUCKET_NAME","S3_VECTOR_TVM_ROLE_ARN":"$TVM_ROLE_ARN","AWS_REGION":"$REGION"},
)
print(f"RUNTIME_ARN={result.agent_arn}")
print(f"RUNTIME_ID={result.agent_id}")
PYEOF

  # Capture output
  RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
    --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" \
    --output text --no-cli-pager 2>/dev/null || echo "")
  RUNTIME_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}"

  if [ "${RESTORE_REQS:-0}" = "1" ]; then
    mv "$SCRIPT_DIR/requirements.txt.bak" "$SCRIPT_DIR/requirements.txt"
  fi
fi

# ---------------------------------------------------------------------------
# Step 4: Wait for READY + attach permissions
# ---------------------------------------------------------------------------
if [ -n "$RUNTIME_ID" ] && [ "$RUNTIME_ID" != "None" ]; then
  echo "  Runtime: $RUNTIME_ID"
  for i in $(seq 1 24); do
    STATUS=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --query "status" --output text --no-cli-pager 2>/dev/null || echo "UNKNOWN")
    if [ "$STATUS" = "READY" ]; then echo "  Status: READY ✓"; break; fi
    printf "  Status: %s (%ds)\\r" "$STATUS" $((i*5))
    sleep 5
  done

  # Attach permissions to execution role (for code deploy mode)
  if [ "$DEPLOY_MODE" = "code" ]; then
    EXEC_ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --query "roleArn" --output text --no-cli-pager 2>/dev/null || echo "")
    EXEC_ROLE=$(echo "$EXEC_ROLE_ARN" | sed 's|.*/||')
    if [ -n "$EXEC_ROLE" ] && [ "$EXEC_ROLE" != "None" ]; then
      aws iam put-role-policy --role-name "$EXEC_ROLE" --policy-name "TVMAndS3VectorsAccess" \
        --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"],\"Resource\":\"${TVM_ROLE_ARN}\"},{\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\",\"bedrock:Converse\",\"bedrock:ConverseStream\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"s3vectors:*\"],\"Resource\":[\"arn:aws:s3vectors:${REGION}:${ACCOUNT_ID}:bucket/${VECTOR_BUCKET_NAME}\",\"arn:aws:s3vectors:${REGION}:${ACCOUNT_ID}:bucket/${VECTOR_BUCKET_NAME}/*\"]}]}" \
        --no-cli-pager 2>/dev/null
      echo "  Permissions attached: $EXEC_ROLE"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Write env file
# ---------------------------------------------------------------------------
ENCODED_ARN=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${RUNTIME_ARN}', safe=''))")
AGENT_ENDPOINT="https://bedrock-agentcore.${REGION}.amazonaws.com/runtimes/${ENCODED_ARN}/invocations?qualifier=DEFAULT"
cat > "$SCRIPT_DIR/.lab4_env" <<EOF
export LAB4_RUNTIME_ID="$RUNTIME_ID"
export LAB4_AGENT_ENDPOINT="$AGENT_ENDPOINT"
export S3_VECTOR_BUCKET_NAME="$VECTOR_BUCKET_NAME"
export S3_VECTOR_TVM_ROLE_ARN="$TVM_ROLE_ARN"
EOF

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Lab 4 Agent Deployed (S3 Vector Memory)                   ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Mode:   $DEPLOY_MODE"
echo "║  Bucket: $VECTOR_BUCKET_NAME"
echo "║  TVM:    $TVM_ROLE_ARN"
echo "║  Auth:   JWT (Cognito)                                       ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Run:  source .lab4_env"
echo ""
if [ "$DEPLOY_MODE" = "code" ]; then
  echo "If you encounter 'Runtime initialization time exceeded' errors,"
  echo "redeploy with container mode:  bash deploy.sh --container"
  echo ""
fi
