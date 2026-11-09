#!/usr/bin/env bash
# setup_tvm_role.sh — Creates the AgentCore TVM IAM role for Module 3 Lab 1 Task 1
# Usage: bash setup_tvm_role.sh <memory-arn>
set -euo pipefail

MEMORY_ARN="${1:?Usage: bash setup_tvm_role.sh <memory-arn>}"
ROLE_NAME="workshop-module3-lab1-agentcore-tvm-role"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-us-east-1}"

echo "→ Account : $ACCOUNT_ID"
echo "→ Region  : $REGION"
echo "→ Memory  : $MEMORY_ARN"
echo "→ Role    : $ROLE_NAME"
echo ""

# ---------------------------------------------------------------------------
# Trust policy — allows the current account to assume + tag the role
# ---------------------------------------------------------------------------
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::${ACCOUNT_ID}:root" },
      "Action": ["sts:AssumeRole", "sts:TagSession"]
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
      "Action": ["sts:AssumeRole", "sts:TagSession"]
    }
  ]
}
EOF
)

# ---------------------------------------------------------------------------
# Memory policy — scoped to TenantID session tag via ABAC condition
# ---------------------------------------------------------------------------
MEMORY_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:GetEvent",
        "bedrock-agentcore:ListEvents"
      ],
      "Resource": "${MEMORY_ARN}",
      "Condition": {
        "StringLike": {
          "bedrock-agentcore:actorId": "\${aws:PrincipalTag/TenantID}:*"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:ListMemoryRecords"
      ],
      "Resource": "${MEMORY_ARN}",
      "Condition": {
        "StringLike": {
          "bedrock-agentcore:namespacePath": "/\${aws:PrincipalTag/TenantID}:*"
        }
      }
    }
  ]
}
EOF
)

# ---------------------------------------------------------------------------
# Create or update the role
# ---------------------------------------------------------------------------
if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
  echo "→ Role already exists — updating trust policy ..."
  aws iam update-assume-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-document "$TRUST_POLICY"
else
  echo "→ Creating role $ROLE_NAME ..."
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "TVM role for AgentCore Memory tenant isolation (Module 3 Lab 1)" \
    > /dev/null
fi

echo "→ Attaching memory policy ..."
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "AgentCoreMemoryTenantPolicy" \
  --policy-document "$MEMORY_POLICY"

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)

echo ""
echo "✓ Done. Set these environment variables before running the lab:"
echo ""
echo "  export AGENTCORE_TVM_ROLE_ARN=${ROLE_ARN}"
echo ""
