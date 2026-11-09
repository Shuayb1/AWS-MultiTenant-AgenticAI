#!/usr/bin/env bash
# setup_tvm_role.sh — Create the TVM IAM role for Module 3, Lab 1
#
# This role is assumed by the backend with a TenantID session tag.
# It includes:
#   1. AgentCore session boundary — restricts which sessions a tenant can invoke
#   2. DynamoDB tenant scoping — restricts data access to the tenant's partition
#   3. Bedrock model invocation — allows the agent to call foundation models
#
# The bedrock-agentcore:sessionId condition key is the IAM-enforced boundary
# that prevents cross-tenant session access even if there's an application bug.
set -euo pipefail

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_NAME="workshop-module3-lab1-tvm-role"
REGION=${AWS_REGION:-us-east-1}

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Creating TVM Role with Session Boundary                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Trust policy — allows the backend Lambda/ECS to assume this role
# with sts:TagSession (required to pass TenantID session tag)
# ---------------------------------------------------------------------------
cat > /tmp/tvm-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::${ACCOUNT_ID}:root"},
    "Action": ["sts:AssumeRole", "sts:TagSession"],
    "Condition": {
      "StringEquals": {"sts:ExternalId": "workshop-module3"}
    }
  }]
}
EOF

aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document file:///tmp/tvm-trust-policy.json \
  --tags Key=Workshop,Value=module3-lab1 \
  --no-cli-pager 2>/dev/null || echo "  Role already exists, updating policy..."

# ---------------------------------------------------------------------------
# Permission policy — scoped by TenantID session tag
# ---------------------------------------------------------------------------
cat > /tmp/tvm-permissions.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AgentCoreSessionBoundary",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime",
        "bedrock-agentcore:InvokeAgentRuntimeCommand"
      ],
      "Resource": "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/*",
      "Condition": {
        "StringLike": {
          "bedrock-agentcore:sessionId": "\${aws:PrincipalTag/TenantID}-*"
        }
      }
    },
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DynamoDBTenantScoped",
      "Effect": "Allow",
      "Action": ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:PutItem"],
      "Resource": "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/workshop-*",
      "Condition": {
        "ForAllValues:StringLike": {
          "dynamodb:LeadingKeys": ["\${aws:PrincipalTag/TenantID}#*"]
        }
      }
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "TenantScopedAccess" \
  --policy-document file:///tmp/tvm-permissions.json \
  --no-cli-pager

echo ""
echo "✓ TVM Role created: arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo ""
echo "Policy includes:"
echo "  • AgentCoreSessionBoundary — sessionId must match \"\${TenantID}-*\""
echo "  • BedrockInvoke — model invocation"
echo "  • DynamoDBTenantScoped — leading keys must match \"\${TenantID}#*\""
echo ""
echo "How the session boundary works:"
echo "  Caller with TenantID=tenant-001 can ONLY invoke sessions named \"tenant-001-*\""
echo "  Caller with TenantID=tenant-002 CANNOT invoke session \"tenant-001-conv-abc\""
echo "  → IAM denies at the control plane, before the request reaches agent code"
echo ""
echo "Export:"
echo "  export AGENTCORE_TVM_ROLE_ARN=arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
