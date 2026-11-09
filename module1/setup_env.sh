#!/bin/bash
# setup_env.sh — Load workshop environment variables from CloudFormation stack outputs.
# Run once at the start of Module 1: source setup_env.sh

STACK_NAME=workshop-infrastructure
export AWS_REGION=${AWS_REGION:-us-east-1}

echo "Loading environment from stack: $STACK_NAME (region: $AWS_REGION)"

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

export COGNITO_USER_POOL_ID=$(get_output CognitoUserPoolId)
export COGNITO_CLIENT_ID=$(get_output CognitoClientId)
export API_GATEWAY_URL=$(get_output ApiGatewayUrl)
export AUTHORIZER_FUNCTION_NAME=$(get_output AuthorizerFunctionName)
export DOCUMENTS_FUNCTION_NAME=$(get_output DocumentsFunctionName)
export DOCUMENTS_API_URL=$(get_output DocumentsApiUrl)
export TENANT_ISOLATION_ROLE_ARN=$(get_output TenantIsolationRoleArn)
export OPENSEARCH_ENDPOINT=$(get_output OpenSearchEndpoint)
export AURORA_CLUSTER_ARN=$(get_output AuroraClusterArn)
export AURORA_SECRET_ARN=$(get_output DBSecret)

# Test credentials pre-created by the workshop setup
export TEST_USERNAME1=testuser1@example.com
export TEST_PASSWORD=Workshop@123!
export TEST_USERNAME2=testuser2@example.com

# Validate
MISSING=0
for VAR in COGNITO_USER_POOL_ID COGNITO_CLIENT_ID API_GATEWAY_URL AUTHORIZER_FUNCTION_NAME DOCUMENTS_FUNCTION_NAME DOCUMENTS_API_URL TENANT_ISOLATION_ROLE_ARN OPENSEARCH_ENDPOINT; do
  VAL=$(eval echo "\$$VAR")
  if [ -z "$VAL" ]; then
    echo "  ERROR: $VAR is empty — check stack outputs in the AWS Console"
    MISSING=1
  else
    echo "  $VAR=$VAL"
  fi
done

if [ $MISSING -eq 0 ]; then
  echo "Environment ready."
else
  echo "One or more variables failed to load. Check CloudFormation → Stacks → $STACK_NAME → Outputs."
fi
