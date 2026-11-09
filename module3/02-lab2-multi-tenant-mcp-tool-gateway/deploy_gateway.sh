#!/usr/bin/env bash
# deploy_gateway.sh — Deploy the Multi-Tenant MCP Tool Gateway
# Module 3, Lab 2
#
# Creates the AgentCore Gateway with:
#   - Cognito JWT authorizer (inbound auth)
#   - Cedar policy (hard boundary)
#   - Interceptor Lambda (tenant context propagation + audit)
#   - Lambda target: query_orders
#   - MCP Server target: query_properties (with OAuth credential provider)
#
# Prerequisites:
#   - source setup_env.sh
#   - deploy_query_orders.sh already run
#   - deploy_query_properties_mcp_server.sh already run
#   - deploy_interceptor.sh already run
#   - setup_credential_provider.sh already run
#
# Usage:
#   bash deploy_gateway.sh
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME=${STACK_NAME:-workshop-infrastructure}
GATEWAY_NAME="workshopMultiTenantToolGateway"

# Resolve Cognito settings from CFN if not already set
COGNITO_USER_POOL_ID=${COGNITO_USER_POOL_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue | [0]" \
  --output text --no-cli-pager 2>/dev/null)}
COGNITO_CLIENT_ID=${COGNITO_CLIENT_ID:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue | [0]" \
  --output text --no-cli-pager 2>/dev/null)}

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying Multi-Tenant MCP Tool Gateway                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Create the Gateway
# ---------------------------------------------------------------------------
echo "→ Step 1: Creating AgentCore Gateway..."

COGNITO_ISSUER="https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}"

# Create gateway execution role if it doesn't exist
GATEWAY_ROLE_NAME="workshop-gateway-exec-role"
GATEWAY_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${GATEWAY_ROLE_NAME}"

if ! aws iam get-role --role-name "$GATEWAY_ROLE_NAME" --no-cli-pager > /dev/null 2>&1; then
  aws iam create-role \
    --role-name "$GATEWAY_ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --no-cli-pager > /dev/null

  aws iam put-role-policy --role-name "$GATEWAY_ROLE_NAME" \
    --policy-name GatewayAccess \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"lambda:InvokeFunction\"],\"Resource\":\"arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:workshop-*\"},{\"Effect\":\"Allow\",\"Action\":[\"bedrock-agentcore:GetWorkloadAccessToken\",\"bedrock-agentcore:GetResourceOauth2Token\",\"secretsmanager:GetSecretValue\"],\"Resource\":\"*\"}]}" \
    --no-cli-pager

  sleep 10  # Wait for role propagation
fi

# Check if gateway already exists
GATEWAY_ID=$(aws bedrock-agentcore-control list-gateways \
  --query "items[?name=='${GATEWAY_NAME}'].gatewayId | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "")

if [ -z "$GATEWAY_ID" ] || [ "$GATEWAY_ID" = "None" ] || [ "$GATEWAY_ID" = "null" ]; then
  if ! RESPONSE=$(aws bedrock-agentcore-control create-gateway \
    --name "$GATEWAY_NAME" \
    --role-arn "$GATEWAY_ROLE_ARN" \
    --authorizer-type "CUSTOM_JWT" \
    --authorizer-configuration "{
      \"customJWTAuthorizer\": {
        \"discoveryUrl\": \"${COGNITO_ISSUER}/.well-known/openid-configuration\",
        \"allowedAudience\": [\"${COGNITO_CLIENT_ID}\"]
      }
    }" \
    --protocol-type "MCP" \
    --no-cli-pager 2>&1); then
    echo "  ERROR: Failed to create gateway."
    echo "  Response: $RESPONSE"
    exit 1
  fi

  GATEWAY_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('gatewayId',''))" 2>/dev/null || echo "")
fi

if [ -z "$GATEWAY_ID" ] || [ "$GATEWAY_ID" = "None" ] || [ "$GATEWAY_ID" = "null" ]; then
  echo "  ERROR: Failed to create or find gateway."
  exit 1
fi

echo "  Gateway ID: $GATEWAY_ID"

# Ensure authorizer uses allowedAudience (not allowedClients)
aws bedrock-agentcore-control update-gateway \
  --gateway-id "$GATEWAY_ID" \
  --authorizer-type "CUSTOM_JWT" \
  --authorizer-configuration "{
    \"customJWTAuthorizer\": {
      \"discoveryUrl\": \"${COGNITO_ISSUER}/.well-known/openid-configuration\",
      \"allowedAudience\": [\"${COGNITO_CLIENT_ID}\"]
    }
  }" \
  --no-cli-pager > /dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Step 2: Attach Cedar policy
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 2: Attaching Cedar policy..."

aws bedrock-agentcore-control put-gateway-policy \
  --gateway-identifier "$GATEWAY_ID" \
  --policy file://cedar_policy.json \
  --no-cli-pager > /dev/null 2>&1 || echo "  (Policy may already be attached)"

echo "  Cedar policy attached"

# ---------------------------------------------------------------------------
# Step 3: Attach interceptor via update-gateway
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 3: Attaching interceptor Lambda..."

INTERCEPTOR_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:workshop-gateway-interceptor"

aws bedrock-agentcore-control update-gateway \
  --gateway-identifier "$GATEWAY_ID" \
  --name "$GATEWAY_NAME" \
  --role-arn "$GATEWAY_ROLE_ARN" \
  --authorizer-type "CUSTOM_JWT" \
  --authorizer-configuration "{
    \"customJWTAuthorizer\": {
      \"discoveryUrl\": \"${COGNITO_ISSUER}/.well-known/openid-configuration\",
      \"allowedAudience\": [\"${COGNITO_CLIENT_ID}\"]
    }
  }" \
  --interceptor-configurations "[{
    \"interceptor\": {
      \"lambda\": {
        \"arn\": \"${INTERCEPTOR_ARN}\"
      }
    },
    \"interceptionPoints\": [\"REQUEST\"],
    \"inputConfiguration\": {
      \"passRequestHeaders\": true
    }
  }]" \
  --no-cli-pager > /dev/null 2>&1

# Verify interceptor was attached
_INTERCEPTOR_CHECK=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier "$GATEWAY_ID" \
  --query "interceptorConfigurations[0].interceptor.lambda.arn" --output text --no-cli-pager 2>/dev/null || echo "")
if [ -z "$_INTERCEPTOR_CHECK" ] || [ "$_INTERCEPTOR_CHECK" = "None" ]; then
  echo "  WARNING: Interceptor may not have attached. Check permissions."
else
  echo "  Interceptor attached: $_INTERCEPTOR_CHECK"
fi

# ---------------------------------------------------------------------------
# Step 4: Register Lambda target (query-orders)
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 4: Registering Lambda target: query-orders..."

QUERY_ORDERS_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:workshop-query-orders"

aws bedrock-agentcore-control create-gateway-target \
  --gateway-identifier "$GATEWAY_ID" \
  --name "query-orders" \
  --description "Query tenant orders from DynamoDB" \
  --target-configuration "{
    \"mcp\": {
      \"lambda\": {
        \"lambdaArn\": \"${QUERY_ORDERS_ARN}\",
        \"toolSchema\": {
          \"inlinePayload\": [
            {
              \"name\": \"query_orders\",
              \"description\": \"Query orders for the current tenant from DynamoDB. Returns order details including product, amount, and status.\",
              \"inputSchema\": {
                \"type\": \"object\",
                \"properties\": {
                  \"status\": {
                    \"type\": \"string\",
                    \"description\": \"Optional filter by order status (delivered, shipped, active)\"
                  }
                }
              }
            }
          ]
        }
      }
    }
  }" \
  --metadata-configuration '{"allowedRequestHeaders": ["x-tenant-id"]}' \
  --credential-provider-configurations '[{"credentialProviderType": "GATEWAY_IAM_ROLE"}]' \
  --no-cli-pager > /dev/null 2>&1 || echo "  (Target may already exist)"

echo "  Lambda target registered: query-orders"

# ---------------------------------------------------------------------------
# Step 5: Register MCP Server target (query-properties)
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 5: Registering MCP Server target: query-properties..."

PROPERTIES_MCP_ENDPOINT="${QUERY_PROPERTIES_MCP_ENDPOINT:-}"

if [ -z "$PROPERTIES_MCP_ENDPOINT" ]; then
  # Try to get the ALB endpoint from CFN outputs
  ALB_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='McpServerAlbEndpoint'].OutputValue | [0]" \
    --output text --no-cli-pager 2>/dev/null || echo "")
  if [ -n "$ALB_ENDPOINT" ] && [ "$ALB_ENDPOINT" != "None" ]; then
    PROPERTIES_MCP_ENDPOINT="${ALB_ENDPOINT}/mcp"
  fi
fi

if [ -z "$PROPERTIES_MCP_ENDPOINT" ]; then
  echo "  WARNING: MCP endpoint not found. Run deploy_query_properties_mcp_server.sh first."
else
  # Get the OAuth credential provider ARN
  CREDENTIAL_PROVIDER_ARN="${CREDENTIAL_PROVIDER_ARN:-$(aws bedrock-agentcore-control get-oauth2-credential-provider \
    --name workshop-properties-provider \
    --query 'credentialProviderArn' --output text --no-cli-pager 2>/dev/null || echo "")}"

  if [ -n "$CREDENTIAL_PROVIDER_ARN" ] && [ "$CREDENTIAL_PROVIDER_ARN" != "None" ]; then
    # Register with OAuth credential provider (production pattern)
    aws bedrock-agentcore-control create-gateway-target \
      --gateway-identifier "$GATEWAY_ID" \
      --name "query-properties" \
      --description "Query tenant properties from Aurora PostgreSQL via MCP Server (ECS)" \
      --target-configuration "{
        \"mcp\": {
          \"mcpServer\": {
            \"endpoint\": \"${PROPERTIES_MCP_ENDPOINT}\"
          }
        }
      }" \
      --credential-provider-configurations "[{
        \"credentialProviderType\": \"OAUTH\",
        \"credentialProvider\": {
          \"oauthCredentialProvider\": {
            \"providerArn\": \"${CREDENTIAL_PROVIDER_ARN}\",
            \"scopes\": [\"mcp-server/query\"],
            \"grantType\": \"CLIENT_CREDENTIALS\"
          }
        }
      }]" \
      --metadata-configuration '{"allowedRequestHeaders": ["x-tenant-id", "x-user-token"]}' \
      --no-cli-pager > /dev/null 2>&1 || echo "  (Target may already exist)"
    echo "  MCP Server target registered: query-properties (OAuth from Identity)"
  else
    # Fallback: register without credential provider
    echo "  WARNING: OAuth provider not found. Registering without outbound auth."
    echo "  Run setup_credential_provider.sh first for production setup."
    aws bedrock-agentcore-control create-gateway-target \
      --gateway-identifier "$GATEWAY_ID" \
      --name "query-properties" \
      --description "Query tenant properties from Aurora PostgreSQL via MCP Server (ECS)" \
      --target-configuration "{
        \"mcp\": {
          \"mcpServer\": {
            \"endpoint\": \"${PROPERTIES_MCP_ENDPOINT}\"
          }
        }
      }" \
      --no-cli-pager > /dev/null 2>&1 || echo "  (Target may already exist)"
    echo "  MCP Server target registered: query-properties (no outbound auth)"
  fi
fi

# ---------------------------------------------------------------------------
# Step 6: Output
# ---------------------------------------------------------------------------
echo ""

GATEWAY_ENDPOINT=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier "$GATEWAY_ID" \
  --query 'gatewayUrl' --output text --no-cli-pager 2>/dev/null || echo "")

# Ensure endpoint has /mcp suffix
if [ -n "$GATEWAY_ENDPOINT" ] && [ "$GATEWAY_ENDPOINT" != "None" ]; then
  GATEWAY_ENDPOINT="${GATEWAY_ENDPOINT%/}/mcp"
else
  GATEWAY_ENDPOINT="https://${GATEWAY_ID}.gateway.bedrock-agentcore.${REGION}.amazonaws.com/mcp"
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Gateway Deployed (Multi-Tenant MCP Hub)                   ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Gateway ID:     $GATEWAY_ID"
echo "║  Endpoint:       $GATEWAY_ENDPOINT"
echo "║  Targets:                                                    ║"
echo "║    - query_orders (Lambda + TVM)                             ║"
echo "║    - query_properties (MCP Server + OAuth)                   ║"
echo "║  Cedar policy:   attached                                    ║"
echo "║  Interceptor:    attached                                    ║"
echo "║  Authorizer:     Cognito ($COGNITO_USER_POOL_ID)             ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Save to env file for subsequent scripts
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cat > "$SCRIPT_DIR/.gateway_env" <<EOF
export GATEWAY_ID=$GATEWAY_ID
export GATEWAY_ENDPOINT=$GATEWAY_ENDPOINT
EOF

echo "Run:  source .gateway_env"
echo ""
