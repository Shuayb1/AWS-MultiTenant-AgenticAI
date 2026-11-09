#!/usr/bin/env bash
# deploy.sh — Deploy the Lab 2 agent using AgentCore Starter Toolkit
# Module 3, Lab 2
#
# Uses bedrock-agentcore-starter-toolkit to build and deploy the agent
# via direct code deployment. The toolkit handles packaging, S3 upload,
# and runtime creation with proper ARM64 dependencies.
#
# Prerequisites:
#   - source setup_env.sh
#   - Gateway deployed (deploy_gateway.sh)
#   - agent.py patched with Task 2.6 solution
#   - pip install bedrock-agentcore-starter-toolkit (pre-installed in workshop)
#
# Usage:
#   bash deploy.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME="${STACK_NAME:-workshop-infrastructure}"
AGENT_NAME="workshopModule3Lab2Agent"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying Lab 2 Agent (Starter Toolkit)                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Resolve configuration
# ---------------------------------------------------------------------------
echo "→ Step 1: Resolving configuration..."

COGNITO_USER_POOL_ID="${COGNITO_USER_POOL_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue | [0]" \
  --output text --no-cli-pager)}"
COGNITO_CLIENT_ID="${COGNITO_CLIENT_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue | [0]" \
  --output text --no-cli-pager)}"
COGNITO_DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}/.well-known/openid-configuration"

# Resolve Gateway endpoint
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/.gateway_env" ]; then
  source "$SCRIPT_DIR/.gateway_env"
fi
GATEWAY_ENDPOINT="${GATEWAY_ENDPOINT:-}"
if [ -z "$GATEWAY_ENDPOINT" ] || [ "$GATEWAY_ENDPOINT" = "None" ] || [ "$GATEWAY_ENDPOINT" = "null" ]; then
  GATEWAY_ID="${GATEWAY_ID:-$(aws bedrock-agentcore-control list-gateways \
    --query "items[0].gatewayId" \
    --output text --no-cli-pager 2>/dev/null || echo "")}"
  if [ -n "$GATEWAY_ID" ] && [ "$GATEWAY_ID" != "None" ] && [ "$GATEWAY_ID" != "null" ]; then
    GATEWAY_ENDPOINT="https://${GATEWAY_ID}.gateway.bedrock-agentcore.${REGION}.amazonaws.com/mcp"
  else
    echo "  ERROR: Gateway not found. Run deploy_gateway.sh first."
    exit 1
  fi
fi

echo "  Cognito:  $COGNITO_USER_POOL_ID"
echo "  Gateway:  $GATEWAY_ENDPOINT"
echo ""

# ---------------------------------------------------------------------------
# Step 2: Configure and deploy via starter toolkit
# ---------------------------------------------------------------------------
echo "→ Step 2: Deploying agent via starter toolkit..."
echo ""

# Ensure starter toolkit is installed
if ! python3 -c "import bedrock_agentcore_starter_toolkit" 2>/dev/null; then
  echo "  Installing bedrock-agentcore-starter-toolkit..."
  pip install -q bedrock-agentcore-starter-toolkit
fi

# Remove stale toolkit config (prevents update attempts on deleted runtimes)
rm -f "$SCRIPT_DIR/.bedrock_agentcore.yaml"

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
GATEWAY_EP   = "$GATEWAY_ENDPOINT"
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
        "GATEWAY_ENDPOINT": GATEWAY_EP,
        "AWS_REGION": REGION,
    },
)

print(f"")
print(f"  Agent Runtime ARN: {result.agent_arn}")
print(f"  Agent Runtime ID:  {result.agent_id}")

# Write env file
import urllib.parse
encoded_arn = urllib.parse.quote(result.agent_arn, safe="")
endpoint = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
env_file = SCRIPT_DIR / ".lab2_env"
env_file.write_text(
    f'export LAB2_RUNTIME_ID="{result.agent_id}"\n'
    f'export LAB2_AGENT_ENDPOINT="{endpoint}"\n'
    f'export GATEWAY_ENDPOINT="{GATEWAY_EP}"\n'
    f'export GATEWAY_ID="{GATEWAY_EP.split("//")[1].split(".")[0] if "//" in GATEWAY_EP else ""}"\n'
)
PYEOF

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Lab 2 Agent Deployed                                      ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Gateway:   $GATEWAY_ENDPOINT"
echo "║  Auth:      JWT (Cognito)                                    ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Run:  source .lab2_env"
echo ""
