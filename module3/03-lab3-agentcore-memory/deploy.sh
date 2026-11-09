#!/usr/bin/env bash
# deploy.sh — One-command deploy for Lab 3 (AgentCore Memory)
# Module 3, Lab 3
#
# This script handles everything:
#   1. Creates AgentCore Memory (if not exists)
#   2. Creates/updates TVM IAM role with correct memory ARN
#   3. Deploys the agent via starter toolkit
#
# Prerequisites:
#   - source setup_env.sh
#   - agentcore_memory_session_manager.py patched (Tasks 3.3-3.5)
#
# Usage:
#   bash deploy.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME="${STACK_NAME:-workshop-infrastructure}"
AGENT_NAME="workshopModule3Lab3MemoryAgent"
TVM_ROLE_NAME="${STACK_NAME}-lab3-memory-tvm-role"
MEMORY_EXEC_ROLE="arn:aws:iam::${ACCOUNT_ID}:role/${STACK_NAME}-agentcore-memory-exec-role"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying Lab 3 Agent (AgentCore Memory)                   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# Step 1: Delete existing memory and agent, create fresh
# ---------------------------------------------------------------------------
echo "→ Step 1: Creating fresh AgentCore Memory..."

# Delete existing agent runtime
EXISTING_RUNTIME=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "")
if [ -n "$EXISTING_RUNTIME" ] && [ "$EXISTING_RUNTIME" != "None" ]; then
  echo "  Deleting existing agent: $EXISTING_RUNTIME"
  aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$EXISTING_RUNTIME" --no-cli-pager 2>/dev/null || true
  sleep 15
fi

# Delete existing memories
python3 -c "
import boto3, time
client = boto3.client('bedrock-agentcore-control', region_name='$REGION')
memories = client.list_memories().get('memories', [])
for m in memories:
    print(f\"  Deleting memory: {m['id']}\")
    try:
        client.delete_memory(memoryId=m['id'])
    except Exception as e:
        print(f\"  (skip: {e})\")
# Wait for deletions to complete
for _ in range(24):
    remaining = client.list_memories().get('memories', [])
    if not remaining:
        break
    time.sleep(5)
"

# Create new memory
echo "  Creating new memory..."
MEMORY_ID=$(python3 -c "
import boto3, json, time, sys
client = boto3.client('bedrock-agentcore-control', region_name='$REGION')
# Long-term memory strategies. Namespaces MUST lead with {actorId} so the
# resolved path (/tenant-001:workshop-agent:<sub>/...) matches the TVM role's
# bedrock-agentcore:namespace condition: StringLike '/\${aws:PrincipalTag/TenantID}:*'.
resp = client.create_memory(
    name='workshop_memory',
    memoryExecutionRoleArn='$MEMORY_EXEC_ROLE',
    eventExpiryDuration=90,
    memoryStrategies=[
        {'userPreferenceMemoryStrategy': {
            'name': 'TenantUserPreferences',
            'namespaces': ['/{actorId}/preferences'],
        }},
        {'semanticMemoryStrategy': {
            'name': 'TenantSemanticFacts',
            'namespaces': ['/{actorId}/facts'],
        }},
    ],
)
# Extract ID from response
memory_id = resp.get('memoryId', resp.get('id', ''))
if not memory_id:
    arn = resp.get('arn', resp.get('memoryArn', ''))
    if arn:
        memory_id = arn.split('/')[-1]
if not memory_id:
    time.sleep(3)
    memories = client.list_memories().get('memories', [])
    for m in memories:
        if 'workshop_memory' in m.get('id', ''):
            memory_id = m['id']
            break
if not memory_id:
    print('ERROR', file=sys.stderr)
    sys.exit(1)
# Print ID immediately so bash captures it
print(memory_id, flush=True)
" 2>&1)

if [ -z "$MEMORY_ID" ] || [ "$MEMORY_ID" = "ERROR" ]; then
  echo "  ERROR: Failed to create memory."
  exit 1
fi

# Wait for ACTIVE in bash (visible progress).
# NOTE: get-memory returns status under memory.status, not a top-level status
# field. Query both so this works regardless of CLI/SDK shape, and FAIL LOUDLY
# if it never goes ACTIVE — proceeding with a non-ACTIVE memory silently breaks
# strategy extraction (LTM) later.
echo "  Memory ID: $MEMORY_ID — waiting for ACTIVE..."
MEMORY_ACTIVE=0
for i in $(seq 1 60); do
  STATUS=$(aws bedrock-agentcore-control get-memory --memory-id "$MEMORY_ID" \
    --query "memory.status || status" --output text --no-cli-pager 2>/dev/null || echo "UNKNOWN")
  if [ "$STATUS" = "ACTIVE" ]; then
    echo "  Memory ACTIVE ✓            "
    MEMORY_ACTIVE=1
    break
  fi
  printf "  Status: %s (%ds)\\r" "$STATUS" $((i*5))
  sleep 5
done

if [ "$MEMORY_ACTIVE" != "1" ]; then
  echo ""
  echo "  ERROR: Memory $MEMORY_ID did not reach ACTIVE within 300s (last status: $STATUS)."
  echo "         Long-term memory extraction will not work until it is ACTIVE."
  echo "         Re-run deploy.sh, or check: aws bedrock-agentcore-control get-memory --memory-id $MEMORY_ID"
  exit 1
fi

MEMORY_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:memory/${MEMORY_ID}"

# ---------------------------------------------------------------------------
# Step 2: Create or update TVM IAM role
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 2: Ensuring TVM role exists with correct memory ARN..."

TVM_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${TVM_ROLE_NAME}"

TRUST_POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"AWS\":\"arn:aws:iam::${ACCOUNT_ID}:root\"},\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"]},{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"bedrock-agentcore.amazonaws.com\"},\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"]}]}"

if aws iam get-role --role-name "$TVM_ROLE_NAME" --no-cli-pager &>/dev/null; then
  aws iam update-assume-role-policy --role-name "$TVM_ROLE_NAME" --policy-document "$TRUST_POLICY" --no-cli-pager
else
  aws iam create-role --role-name "$TVM_ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "TVM role for AgentCore Memory tenant isolation" \
    --no-cli-pager > /dev/null
  sleep 10
fi

# Attach memory policy scoped to the correct memory ARN
MEMORY_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["bedrock-agentcore:CreateEvent","bedrock-agentcore:GetEvent","bedrock-agentcore:ListEvents"],
      "Resource": "${MEMORY_ARN}",
      "Condition": {"StringLike": {"bedrock-agentcore:actorId": "\${aws:PrincipalTag/TenantID}:*"}}
    },
    {
      "Effect": "Allow",
      "Action": ["bedrock-agentcore:RetrieveMemoryRecords","bedrock-agentcore:ListMemoryRecords"],
      "Resource": "${MEMORY_ARN}",
      "Condition": {"StringLike": {"bedrock-agentcore:namespacePath": "/\${aws:PrincipalTag/TenantID}:*"}}
    }
  ]
}
EOF
)
aws iam put-role-policy --role-name "$TVM_ROLE_NAME" \
  --policy-name "AgentCoreMemoryTenantPolicy" \
  --policy-document "$MEMORY_POLICY" --no-cli-pager

echo "  TVM Role: $TVM_ROLE_ARN"
echo "  Memory:   $MEMORY_ARN"

# ---------------------------------------------------------------------------
# Step 3: Resolve Cognito settings
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 3: Resolving Cognito..."

COGNITO_USER_POOL_ID="${COGNITO_USER_POOL_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue | [0]" \
  --output text --no-cli-pager)}"
COGNITO_CLIENT_ID="${COGNITO_CLIENT_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue | [0]" \
  --output text --no-cli-pager)}"

echo "  Cognito: $COGNITO_USER_POOL_ID"

# ---------------------------------------------------------------------------
# Step 4: Deploy agent via starter toolkit
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 4: Deploying agent..."

# Ensure starter toolkit is installed
if ! python3 -c "import bedrock_agentcore_starter_toolkit" 2>/dev/null; then
  echo "  Installing bedrock-agentcore-starter-toolkit..."
  pip install -q bedrock-agentcore-starter-toolkit
fi

# Use minimal requirements for deployment
if [ -f "$SCRIPT_DIR/requirements-deploy.txt" ]; then
  cp "$SCRIPT_DIR/requirements.txt" "$SCRIPT_DIR/requirements.txt.bak"
  cp "$SCRIPT_DIR/requirements-deploy.txt" "$SCRIPT_DIR/requirements.txt"
  RESTORE_REQS=1
fi

# Remove stale toolkit config
rm -f "$SCRIPT_DIR/.bedrock_agentcore.yaml"

COGNITO_DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}/.well-known/openid-configuration"

python3 - <<PYEOF
import sys
import urllib.parse
from pathlib import Path

from bedrock_agentcore_starter_toolkit.operations.runtime import (
    configure_bedrock_agentcore,
    launch_bedrock_agentcore,
)

REGION     = "$REGION"
POOL_ID    = "$COGNITO_USER_POOL_ID"
CLIENT_ID  = "$COGNITO_CLIENT_ID"
AGENT_NAME = "$AGENT_NAME"
MEMORY_ID  = "$MEMORY_ID"
TVM_ROLE   = "$TVM_ROLE_ARN"
SCRIPT_DIR = Path("$SCRIPT_DIR")
DISCOVERY  = "$COGNITO_DISCOVERY_URL"

print(f"  Configuring {AGENT_NAME} ...")
configure_bedrock_agentcore(
    agent_name=AGENT_NAME,
    entrypoint_path=SCRIPT_DIR / "agent.py",
    source_path=str(SCRIPT_DIR),
    authorizer_configuration={
        "customJWTAuthorizer": {
            "discoveryUrl": DISCOVERY,
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
        "AGENTCORE_MEMORY_ID": MEMORY_ID,
        "AGENTCORE_TVM_ROLE_ARN": TVM_ROLE,
        "AWS_REGION": REGION,
    },
)

print(f"")
print(f"  Agent Runtime ARN: {result.agent_arn}")
print(f"  Agent Runtime ID:  {result.agent_id}")

encoded_arn = urllib.parse.quote(result.agent_arn, safe="")
endpoint = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
# StopRuntimeSession REST binding. The agent uses a custom JWT authorizer, so
# data-plane calls must present the OAuth Bearer token (not SigV4 via aws cli).
# This endpoint is called with the same Bearer token used for invoke.
stop_endpoint = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/stopruntimesession?qualifier=DEFAULT"
env_file = SCRIPT_DIR / ".lab3_env"
env_file.write_text(
    f'export LAB3_RUNTIME_ID="{result.agent_id}"\n'
    f'export LAB3_AGENT_ARN="{result.agent_arn}"\n'
    f'export LAB3_AGENT_ENDPOINT="{endpoint}"\n'
    f'export LAB3_STOP_ENDPOINT="{stop_endpoint}"\n'
    f'export AGENTCORE_MEMORY_ID="{MEMORY_ID}"\n'
    f'export AGENTCORE_TVM_ROLE_ARN="{TVM_ROLE}"\n'
)
PYEOF

# Restore original requirements.txt if swapped
if [ "${RESTORE_REQS:-0}" = "1" ]; then
  mv "$SCRIPT_DIR/requirements.txt.bak" "$SCRIPT_DIR/requirements.txt"
fi

# Step 5: Ensure execution role has TVM + memory permissions
echo ""
echo "→ Step 5: Attaching TVM permissions to execution role..."
EXEC_ROLE_NAME=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "")
if [ -n "$EXEC_ROLE_NAME" ] && [ "$EXEC_ROLE_NAME" != "None" ]; then
  EXEC_ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$EXEC_ROLE_NAME" \
    --query "roleArn" --output text --no-cli-pager 2>/dev/null || echo "")
  EXEC_ROLE=$(echo "$EXEC_ROLE_ARN" | sed 's|.*/||')
  if [ -n "$EXEC_ROLE" ] && [ "$EXEC_ROLE" != "None" ]; then
    aws iam put-role-policy --role-name "$EXEC_ROLE" \
      --policy-name "TVMAndMemoryAccess" \
      --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"],\"Resource\":\"arn:aws:iam::${ACCOUNT_ID}:role/${TVM_ROLE_NAME}\"},{\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\",\"bedrock:Converse\",\"bedrock:ConverseStream\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"bedrock-agentcore:CreateEvent\",\"bedrock-agentcore:GetEvent\",\"bedrock-agentcore:ListEvents\",\"bedrock-agentcore:RetrieveMemoryRecords\",\"bedrock-agentcore:ListMemoryRecords\"],\"Resource\":\"${MEMORY_ARN}\"}]}" \
      --no-cli-pager 2>/dev/null
    echo "  Attached: $EXEC_ROLE"
  fi
fi

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Lab 3 Agent Deployed (AgentCore Memory)                   ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Memory:  $MEMORY_ID"
echo "║  TVM:     $TVM_ROLE_ARN"
echo "║  Auth:    JWT (Cognito)                                      ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Run:  source .lab3_env"
echo ""
