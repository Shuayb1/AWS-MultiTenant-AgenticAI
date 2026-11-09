#!/usr/bin/env bash
# setup_credential_provider.sh — Create OAuth2 credential provider for MCP Server target
# Module 3, Lab 2
#
# Creates an OAuth2 credential provider in AgentCore Identity that the Gateway
# uses to authenticate to the MCP Server target. Uses Cognito as the OAuth
# authorization server with Client Credentials grant (2LO).
#
# The Gateway fetches a token from this provider before calling the MCP Server.
# The MCP Server validates the token against Cognito JWKS independently.
#
# Prerequisites:
#   - source setup_env.sh
#   - CFN stack deployed with CognitoGatewayClient (confidential client)
#
# Usage:
#   bash setup_credential_provider.sh
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME=${STACK_NAME:-workshop-infrastructure}
PROVIDER_NAME="workshop-properties-provider"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Setting up OAuth2 Credential Provider                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Get Cognito Gateway Client details
# ---------------------------------------------------------------------------
echo "→ Step 1: Resolving Cognito Gateway Client..."

COGNITO_USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue | [0]" \
  --output text --no-cli-pager)

GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoGatewayClientId'].OutputValue | [0]" \
  --output text --no-cli-pager)

# Retrieve the client secret from Cognito
GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$COGNITO_USER_POOL_ID" \
  --client-id "$GATEWAY_CLIENT_ID" \
  --query "UserPoolClient.ClientSecret" \
  --output text --no-cli-pager)

COGNITO_DOMAIN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoDomain'].OutputValue | [0]" \
  --output text --no-cli-pager)

DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}/.well-known/openid-configuration"

echo "  Pool ID:       $COGNITO_USER_POOL_ID"
echo "  Gateway Client: $GATEWAY_CLIENT_ID"
echo "  Domain:        $COGNITO_DOMAIN"

# ---------------------------------------------------------------------------
# Step 2: Create OAuth2 credential provider
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 2: Creating OAuth2 credential provider..."

# Check if it already exists
EXISTING=$(aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name "$PROVIDER_NAME" --no-cli-pager 2>/dev/null || echo "")

if [ -n "$EXISTING" ]; then
  echo "  Provider exists — updating with current client credentials..."
  aws bedrock-agentcore-control update-oauth2-credential-provider \
    --name "$PROVIDER_NAME" \
    --credential-provider-vendor "CustomOauth2" \
    --oauth2-provider-config-input "{
      \"customOauth2ProviderConfig\": {
        \"oauthDiscovery\": {
          \"discoveryUrl\": \"${DISCOVERY_URL}\"
        },
        \"clientId\": \"${GATEWAY_CLIENT_ID}\",
        \"clientSecret\": \"${GATEWAY_CLIENT_SECRET}\"
      }
    }" \
    --no-cli-pager > /dev/null 2>&1 || {
      # If update fails, delete and recreate
      echo "  Update not supported — deleting and recreating..."
      aws bedrock-agentcore-control delete-oauth2-credential-provider \
        --name "$PROVIDER_NAME" --no-cli-pager > /dev/null 2>&1 || true
      sleep 5
      aws bedrock-agentcore-control create-oauth2-credential-provider \
        --name "$PROVIDER_NAME" \
        --credential-provider-vendor "CustomOauth2" \
        --oauth2-provider-config-input "{
          \"customOauth2ProviderConfig\": {
            \"oauthDiscovery\": {
              \"discoveryUrl\": \"${DISCOVERY_URL}\"
            },
            \"clientId\": \"${GATEWAY_CLIENT_ID}\",
            \"clientSecret\": \"${GATEWAY_CLIENT_SECRET}\"
          }
        }" \
        --no-cli-pager > /dev/null
    }
  echo "  Provider updated: $PROVIDER_NAME"
else
  aws bedrock-agentcore-control create-oauth2-credential-provider \
    --name "$PROVIDER_NAME" \
    --credential-provider-vendor "CustomOauth2" \
    --oauth2-provider-config-input "{
      \"customOauth2ProviderConfig\": {
        \"oauthDiscovery\": {
          \"discoveryUrl\": \"${DISCOVERY_URL}\"
        },
        \"clientId\": \"${GATEWAY_CLIENT_ID}\",
        \"clientSecret\": \"${GATEWAY_CLIENT_SECRET}\"
      }
    }" \
    --no-cli-pager > /dev/null

  echo "  Provider created: $PROVIDER_NAME"
fi

# Verify
PROVIDER_ARN=$(aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name "$PROVIDER_NAME" \
  --query "credentialProviderArn" --output text --no-cli-pager 2>/dev/null || echo "")

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   OAuth2 Credential Provider Configured                     ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Provider:  $PROVIDER_NAME"
echo "║  ARN:       $PROVIDER_ARN"
echo "║  Auth:      Cognito Client Credentials (2LO)                 ║"
echo "║  Token URL: ${COGNITO_DOMAIN}/oauth2/token"
echo "║                                                              ║"
echo "║  How it works:                                               ║"
echo "║    1. Gateway calls Identity: get token for this provider    ║"
echo "║    2. Identity calls Cognito token endpoint (client creds)   ║"
echo "║    3. Cognito returns access token                           ║"
echo "║    4. Gateway passes token to MCP Server                     ║"
echo "║    5. MCP Server validates via JWKS                          ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Export for Gateway registration:"
echo "  export CREDENTIAL_PROVIDER_ARN=$PROVIDER_ARN"
