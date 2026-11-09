#!/usr/bin/env bash
# deploy.sh — Deploy the Lab 5 metering agent (self-contained)
# Module 3, Lab 5
#
# Deploys its own agent with TenantMeteringPlugin.
# No dependency on prior labs.
#
# Usage:
#   bash deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
STACK_NAME=${STACK_NAME:-workshop-infrastructure}
AGENT_NAME="workshopModule3Lab5MeteringAgent"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying Lab 5 Agent (Per-Tenant Metering)               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Resolve Cognito ───────────────────────────────────────────────────────
COGNITO_USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue" \
  --output text --no-cli-pager)
COGNITO_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue" \
  --output text --no-cli-pager)
TEST_PASSWORD="Workshop@123!"

echo "→ Step 1: Resolving configuration..."
echo "  Cognito: $COGNITO_USER_POOL_ID"

# ── Ensure uv is available ────────────────────────────────────────────────
if ! command -v uv &> /dev/null; then
  echo "  Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
  export PATH="$HOME/.local/bin:$PATH"
fi

# ── Deploy via starter toolkit ────────────────────────────────────────────
echo ""
echo "→ Step 2: Deploying agent via starter toolkit..."

pip install bedrock-agentcore-starter-toolkit --quiet --upgrade 2>/dev/null

rm -f .bedrock_agentcore.yaml

export COGNITO_ISSUER COGNITO_CLIENT_ID AGENT_NAME
export AGENTCORE_SUPPRESS_RECOMMENDATION=1

python3 - <<PYEOF
import sys
from pathlib import Path

from bedrock_agentcore_starter_toolkit.operations.runtime import (
    configure_bedrock_agentcore,
    launch_bedrock_agentcore,
)

REGION       = "$REGION"
POOL_ID      = "$COGNITO_USER_POOL_ID"
CLIENT_ID    = "$COGNITO_CLIENT_ID"
AGENT_NAME   = "$AGENT_NAME"
SCRIPT_DIR   = Path("$SCRIPT_DIR")

DISCOVERY_URL = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/openid-configuration"

print(f"  Configuring {AGENT_NAME} ...")
configure_bedrock_agentcore(
    agent_name=AGENT_NAME,
    entrypoint_path=SCRIPT_DIR / "agent.py",
    source_path=str(SCRIPT_DIR),
    authorizer_configuration={
        "customJWTAuthorizer": {
            "discoveryUrl": DISCOVERY_URL,
            "allowedAudience": [CLIENT_ID],
        }
    },
    request_header_configuration={
        "requestHeaderAllowlist": ["Authorization"]
    },
    region=REGION,
    non_interactive=True,
    runtime_type="PYTHON_3_13",
)

print(f"  Launching {AGENT_NAME} ...")
result = launch_bedrock_agentcore(
    config_path=SCRIPT_DIR / ".bedrock_agentcore.yaml",
    agent_name=AGENT_NAME,
    use_codebuild=True,
    auto_update_on_conflict=True,
    env_vars={
        "AWS_REGION": REGION,
    },
)
PYEOF

# ── Resolve runtime and attach CloudWatch permissions ─────────────────────
echo ""
echo "→ Step 3: Resolving runtime and attaching permissions..."

RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "")

# Resolve the execution role that is ACTUALLY attached to THIS runtime.
# Do NOT guess by IAM naming convention: once Labs 1-4 have run, the account
# contains several AmazonBedrockAgentCoreSDKRuntime-* roles and picking [0]
# attaches the CloudWatch policy to the wrong role, so the running agent
# never gets cloudwatch:PutMetricData and no per-tenant metrics appear.
EXEC_ROLE_NAME=""
if [ -n "$RUNTIME_ID" ] && [ "$RUNTIME_ID" != "None" ]; then
  EXEC_ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --query "roleArn" --output text --no-cli-pager 2>/dev/null || echo "")
  # arn:aws:iam::<account>:role/<RoleName> -> <RoleName>
  EXEC_ROLE_NAME=$(echo "$EXEC_ROLE_ARN" | sed 's|.*/||')
fi

echo "  Runtime ID: ${RUNTIME_ID:-not found}"
echo "  Execution Role: ${EXEC_ROLE_NAME:-not found}"

# Attach CloudWatch PutMetricData permission
if [ -n "$EXEC_ROLE_NAME" ] && [ "$EXEC_ROLE_NAME" != "None" ] && [ "$EXEC_ROLE_NAME" != "null" ]; then
  aws iam put-role-policy --role-name "$EXEC_ROLE_NAME" \
    --policy-name CloudWatchMetering \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"cloudwatch:PutMetricData","Resource":"*","Condition":{"StringEquals":{"cloudwatch:namespace":"SaaS/AgentMetering"}}}]}' \
    --no-cli-pager 2>/dev/null && echo "  CloudWatch PutMetricData attached to $EXEC_ROLE_NAME ✓" || echo "  WARNING: Could not attach CloudWatch permission"
else
  echo "  WARNING: Could not resolve execution role."
  echo "  Run manually (resolve the role attached to THIS runtime):"
  echo "    ARN=\$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id $RUNTIME_ID --query roleArn --output text)"
  echo "    ROLE=\${ARN##*/}"
  echo "    aws iam put-role-policy --role-name \$ROLE --policy-name CloudWatchMetering \\"
  echo "      --policy-document '{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"cloudwatch:PutMetricData\",\"Resource\":\"*\",\"Condition\":{\"StringEquals\":{\"cloudwatch:namespace\":\"SaaS/AgentMetering\"}}}]}'"
fi

# ── Build endpoint URL ────────────────────────────────────────────────────

if [ -z "$RUNTIME_ID" ] || [ "$RUNTIME_ID" = "None" ]; then
  echo "  WARNING: Could not find runtime ID. Check deployment logs."
else
  LAB5_AGENT_ENDPOINT="https://bedrock-agentcore.${REGION}.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3A${REGION}%3A${ACCOUNT_ID}%3Aruntime%2F${RUNTIME_ID}/invocations?qualifier=DEFAULT"

  # Save env file
  cat > .lab5_env <<EOF
export LAB5_AGENT_ENDPOINT="${LAB5_AGENT_ENDPOINT}"
export COGNITO_CLIENT_ID="${COGNITO_CLIENT_ID}"
export COGNITO_USER_POOL_ID="${COGNITO_USER_POOL_ID}"
export TEST_PASSWORD="${TEST_PASSWORD}"
EOF

  echo ""
  echo "╔══════════════════════════════════════════════════════════════╗"
  echo "║  Lab 5 Agent Deployed                                      ║"
  echo "╠══════════════════════════════════════════════════════════════╣"
  echo "║  Agent:    $AGENT_NAME"
  echo "║  Runtime:  $RUNTIME_ID"
  echo "║  Plugin:   TenantMeteringPlugin (EMF -> SaaS/AgentMetering)"
  echo "║                                                              ║"
  echo "║  Run:  source .lab5_env                                      ║"
  echo "╚══════════════════════════════════════════════════════════════╝"
fi
