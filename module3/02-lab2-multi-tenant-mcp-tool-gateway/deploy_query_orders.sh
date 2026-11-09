#!/usr/bin/env bash
# deploy_query_orders.sh — Deploy the query_orders Lambda target
# Module 3, Lab 2
#
# Deploys the DynamoDB query tool as a Lambda function.
# The Gateway invokes this Lambda directly (IAM invoke).
# The interceptor injects X-Tenant-ID header before invocation.
#
# Prerequisites:
#   - source setup_env.sh
#
# Usage:
#   bash deploy_query_orders.sh
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME=${STACK_NAME:-workshop-infrastructure}
FUNCTION_NAME="workshop-query-orders"

echo "→ Deploying Lambda target: $FUNCTION_NAME"

# Get the TVM role ARN from CFN outputs
TVM_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='TenantIsolationRoleArn'].OutputValue | [0]" \
  --output text --no-cli-pager)

# Package (include dependencies)
DEPLOY_DIR=$(mktemp -d)
cp query_orders_lambda.py "$DEPLOY_DIR/"
cp token_vending_machine.py "$DEPLOY_DIR/"
pip install cachetools boto3 -t "$DEPLOY_DIR" --quiet 2>/dev/null

DEPLOY_ZIP="/tmp/query-orders.zip"
rm -f "$DEPLOY_ZIP"
(cd "$DEPLOY_DIR" && zip -r "$DEPLOY_ZIP" . --quiet)
rm -rf "$DEPLOY_DIR"

# Check if function exists
if aws lambda get-function --function-name "$FUNCTION_NAME" --no-cli-pager > /dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file fileb://"$DEPLOY_ZIP" \
    --no-cli-pager > /dev/null
  aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"

  aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "Variables={TABLE_NAME=SaaSApplicationData,TVM_ROLE_ARN=${TVM_ROLE_ARN},AWS_REGION_NAME=${REGION}}" \
    --no-cli-pager > /dev/null
  aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
else
  # Create execution role
  ROLE_NAME="${FUNCTION_NAME}-role"
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --no-cli-pager > /dev/null 2>&1 || true

  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
    --no-cli-pager 2>/dev/null || true

  aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name TVMAccess \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"sts:AssumeRole\",\"sts:TagSession\"],\"Resource\":\"${TVM_ROLE_ARN}\"}]}" \
    --no-cli-pager

  sleep 10  # Wait for role propagation

  aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.13 \
    --handler query_orders_lambda.lambda_handler \
    --role "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}" \
    --zip-file fileb://"$DEPLOY_ZIP" \
    --timeout 30 \
    --environment "Variables={TABLE_NAME=SaaSApplicationData,TVM_ROLE_ARN=${TVM_ROLE_ARN},AWS_REGION_NAME=${REGION}}" \
    --no-cli-pager > /dev/null

  aws lambda wait function-active-v2 --function-name "$FUNCTION_NAME"
fi

echo "  Lambda target deployed: $FUNCTION_NAME"
echo "  TVM Role: $TVM_ROLE_ARN"
echo "  Table: SaaSApplicationData"

# ---------------------------------------------------------------------------
# Seed test data (idempotent — skips if data already exists)
# ---------------------------------------------------------------------------
echo ""
echo "→ Seeding DynamoDB test data..."

python3 -c "
import boto3

dynamodb = boto3.resource('dynamodb', region_name='${REGION}')
table = dynamodb.Table('SaaSApplicationData')

# Seed orders for tenant-001
orders_t1 = [
    {'PK': 'tenant-001#orders', 'SK': 'ord-001', 'product': 'Widget Pro', 'amount': '99.99', 'status': 'delivered'},
    {'PK': 'tenant-001#orders', 'SK': 'ord-002', 'product': 'Gadget X', 'amount': '149.99', 'status': 'shipped'},
    {'PK': 'tenant-001#orders', 'SK': 'ord-003', 'product': 'Service Plan', 'amount': '29.99', 'status': 'active'},
]

# Seed orders for tenant-002
orders_t2 = [
    {'PK': 'tenant-002#orders', 'SK': 'ord-004', 'product': 'Enterprise Suite', 'amount': '999.99', 'status': 'delivered'},
    {'PK': 'tenant-002#orders', 'SK': 'ord-005', 'product': 'Support Add-on', 'amount': '49.99', 'status': 'active'},
]

for item in orders_t1 + orders_t2:
    table.put_item(Item=item)

print('  Seeded: 3 orders for tenant-001, 2 orders for tenant-002')
"
