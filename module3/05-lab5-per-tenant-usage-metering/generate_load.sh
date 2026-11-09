#!/usr/bin/env bash
# generate_load.sh — Generate multi-tenant agent requests for metrics visualization
# Module 3, Lab 5
#
# Sends varied workloads as different tenants to produce visible
# per-tenant metrics in CloudWatch. Sends 50 requests per tenant.
#
# Usage:
#   source .lab5_env
#   bash generate_load.sh
set -euo pipefail

source .lab5_env 2>/dev/null || true

if [ -z "${LAB5_AGENT_ENDPOINT:-}" ]; then
  echo "ERROR: LAB5_AGENT_ENDPOINT not set. Run: source .lab5_env"
  exit 1
fi

REQUESTS_PER_TENANT=${1:-50}

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Generating Multi-Tenant Load for Metrics                  ║"
echo "║  Requests per tenant: $REQUESTS_PER_TENANT                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Get tokens for both tenants
TOKEN_TENANT_1=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=testuser1@example.com,PASSWORD=$TEST_PASSWORD \
  --query 'AuthenticationResult.IdToken' --output text --no-cli-pager)

TOKEN_TENANT_2=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=testuser2@example.com,PASSWORD=$TEST_PASSWORD \
  --query 'AuthenticationResult.IdToken' --output text --no-cli-pager)

# Tenant-001: Complex queries (high token usage)
TENANT1_MESSAGES=(
  "Explain the key differences between pool and silo multi-tenant isolation models"
  "What are the security considerations for per-tenant encryption key management?"
  "Describe how to implement tenant-aware rate limiting in a SaaS platform"
  "How do you handle tenant data migration during a schema upgrade?"
  "What metrics should a SaaS platform track for per-tenant billing?"
  "Explain noisy neighbor problems in multi-tenant architectures"
  "How do you implement tenant-specific feature flags?"
  "What is the best approach for multi-tenant observability?"
  "Describe strategies for tenant onboarding automation"
  "How do you handle tenant-specific compliance requirements?"
)

# Tenant-002: Simple queries (low token usage)
TENANT2_MESSAGES=(
  "Hello"
  "Hi"
  "Thanks"
  "OK"
  "Yes"
  "No"
  "Sure"
  "Got it"
  "Bye"
  "Help"
)

# Unique per-run ID for session IDs. AgentCore pins each session to the microVM
# (and agent version) it started on, so reusing session IDs after a redeploy
# would keep hitting the OLD code. A fresh ID per run guarantees new sessions
# on the latest deployed version.
RUN_ID=$(date +%s)

TOTAL=$((REQUESTS_PER_TENANT * 2))
echo "Sending $TOTAL total requests ($REQUESTS_PER_TENANT per tenant)..."
echo ""

COUNTER=0
for i in $(seq 1 $REQUESTS_PER_TENANT); do
  MSG_IDX=$(( (i - 1) % 10 ))

  # Tenant-001 (complex)
  COUNTER=$((COUNTER + 1))
  SESSION_T1="tenant-001-load-${RUN_ID}-$(printf '%033d' $i)"
  echo -ne "  [$COUNTER/$TOTAL] tenant-001 (complex) #$i\r"
  curl -s -X POST "$LAB5_AGENT_ENDPOINT" \
    -H "Authorization: Bearer $TOKEN_TENANT_1" \
    -H "Content-Type: application/json" \
    -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_T1" \
    -d "{\"message\": \"${TENANT1_MESSAGES[$MSG_IDX]}\"}" > /dev/null 2>&1 &

  # Tenant-002 (simple)
  COUNTER=$((COUNTER + 1))
  SESSION_T2="tenant-002-load-${RUN_ID}-$(printf '%033d' $i)"
  echo -ne "  [$COUNTER/$TOTAL] tenant-002 (simple)  #$i\r"
  curl -s -X POST "$LAB5_AGENT_ENDPOINT" \
    -H "Authorization: Bearer $TOKEN_TENANT_2" \
    -H "Content-Type: application/json" \
    -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_T2" \
    -d "{\"message\": \"${TENANT2_MESSAGES[$MSG_IDX]}\"}" > /dev/null 2>&1 &

  # Throttle: wait for both to complete every 2 pairs
  if [ $((i % 2)) -eq 0 ]; then
    wait
  fi
done

wait
echo ""
echo ""
echo "Done. $TOTAL requests sent ($REQUESTS_PER_TENANT complex for tenant-001, $REQUESTS_PER_TENANT simple for tenant-002)."
echo ""
echo "Wait 2-3 minutes, then run:"
echo "  python view_metrics.py"
echo ""
echo "Or view in CloudWatch console:"
echo "  https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#metricsV2?graph=~()&namespace=SaaS/AgentMetering"
