# Module 1: SaaS Foundations — Code

## Directory Structure

```
module1/
├── requirements.txt
├── setup_env.sh                           # Load env vars from CloudFormation
├── setup_env.py                           # Python alternative for env setup
├── 01-lab1-saas-identity/
│   ├── saas_identity.py                   # Lab 1: SaaS identity pipeline (Tasks 1.1–1.3 + verification)
│   ├── authorizer.py                      # Lambda Authorizer (imports saas_identity.py)
│   ├── deploy.sh                          # Package + deploy authorizer to Lambda (Task 1.4)
│   └── setup_dynamodb.py                  # Seed TenantMetadata table (prerequisite)
├── 02-lab2-token-vending-machine/
│   └── token_vending_machine.py           # Lab 2: STS AssumeRole + TTLCache
├── 03-lab3-dynamodb/
│   ├── dynamodb_tenant_repository.py      # Lab 3: DynamoDB tenant repository Lambda
│   └── deploy.sh                          # Package + deploy to Lambda
├── 04-lab4-aurora/
│   ├── aurora_data_api_client.py          # Lab 4: Aurora RDS Data API client
│   ├── setup_rls.sh                       # Create schema, RLS policy, app_user
│   └── deploy.sh                          # Package + deploy to Lambda
└── 05-lab5-opensearch/
    ├── opensearch_tenant_client.py        # Lab 5: OpenSearch index-per-tenant client
    └── deploy.sh                          # Package + deploy to Lambda
```

## Lab → Code Directory Mapping

| Lab | Code Directory |
|-----|---------------|
| Lab 1: SaaS Identity | `01-lab1-saas-identity/` |
| Lab 2: Token Vending Machine | `02-lab2-token-vending-machine/` |
| Lab 3: Amazon DynamoDB | `03-lab3-dynamodb/` |
| Lab 4: Amazon Aurora PostgreSQL | `04-lab4-aurora/` |
| Lab 5: Amazon OpenSearch | `05-lab5-opensearch/` |

## Quick Start

```bash
# Setup environment
source setup_env.sh

# Lab 1 — SaaS Identity
cd 01-lab1-saas-identity
python setup_dynamodb.py        # prerequisite: seed tenant metadata
# Complete Tasks 1.1–1.3 in saas_identity.py
bash deploy.sh                  # Task 1.4: deploy the authorizer
python saas_identity.py         # Task 1.5: verify end-to-end

# Lab 2 — Token Vending Machine
cd ../02-lab2-token-vending-machine
python token_vending_machine.py

# Lab 3 — DynamoDB
cd ../03-lab3-dynamodb
bash deploy.sh
# Test via curl (see lab instructions)

# Lab 4 — Aurora PostgreSQL
cd ../04-lab4-aurora
bash setup_rls.sh               # Create schema + RLS policy
bash deploy.sh
# Test via curl (see lab instructions)

# Lab 5 — OpenSearch
cd ../05-lab5-opensearch
bash deploy.sh
# Test via curl (see lab instructions)
```

## Dependencies

```bash
pip install -r requirements.txt
```

---

## Debugging Guide

Use the commands below to diagnose failures in each lab. Always start by loading environment variables:

```bash
source /workshop/module1/setup_env.sh
```

### Lab 1: SaaS Identity

**Check if TenantMetadata table is seeded:**

```bash
aws dynamodb scan --table-name TenantMetadata \
  --query "Items[].{tenantId: tenantId.S, tier: tier.S, status: status.S}" \
  --output table --no-cli-pager
```

**Check Cognito user attributes (verify custom:tenant_id is set):**

```bash
aws cognito-idp admin-get-user \
  --user-pool-id $COGNITO_USER_POOL_ID \
  --username $TEST_USERNAME1 \
  --query "UserAttributes[?Name=='custom:tenant_id'].Value" --output text
```

**Check Lambda Authorizer logs:**

```bash
aws logs tail "/aws/lambda/$AUTHORIZER_FUNCTION_NAME" --since 5m --format short
```

**Test authentication directly:**

```bash
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=$TEST_USERNAME1,PASSWORD=$TEST_PASSWORD \
  --query 'AuthenticationResult.IdToken' --output text)

# Decode JWT claims (no signature verification)
echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

**Test the authorizer via API Gateway:**

```bash
curl -s "$API_GATEWAY_URL/resource" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Lab 2: Token Vending Machine

**Verify the TVM role exists and has correct trust policy:**

```bash
aws iam get-role --role-name workshop-infrastructure-tenant-isolation-role \
  --query "Role.AssumeRolePolicyDocument" --no-cli-pager | python3 -m json.tool
```

**Test STS AssumeRole directly (simulates what TVM does):**

```bash
aws sts assume-role \
  --role-arn $TENANT_ISOLATION_ROLE_ARN \
  --role-session-name "debug-tenant-001" \
  --tags Key=TenantID,Value=tenant-001 \
  --query "Credentials.AccessKeyId" --output text
```

> If this fails with AccessDenied, the execution role lacks `sts:AssumeRole` + `sts:TagSession` permissions on the TVM role.

### Lab 3: DynamoDB

**Check Lambda logs:**

```bash
aws logs tail "/aws/lambda/$DOCUMENTS_FUNCTION_NAME" --since 5m --format short
```

**Verify DynamoDB table has data:**

```bash
aws dynamodb query --table-name SaaSApplicationData \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values '{":pk":{"S":"tenant-001#DOCUMENT"}}' \
  --query "Count" --output text
```

**Test the API directly:**

```bash
# Create a document
curl -s -X POST "$API_GATEWAY_URL/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "test-001", "title": "Debug Test", "status": "draft"}'

# List documents
curl -s "$API_GATEWAY_URL/documents" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

**Check IAM condition on TVM role (LeadingKeys):**

```bash
aws iam list-role-policies --role-name workshop-infrastructure-tenant-isolation-role --no-cli-pager
aws iam get-role-policy \
  --role-name workshop-infrastructure-tenant-isolation-role \
  --policy-name TenantIsolationPolicy --no-cli-pager | python3 -m json.tool
```

### Lab 4: Aurora PostgreSQL

**Check Lambda logs:**

```bash
aws logs tail "/aws/lambda/workshop-infrastructure-aurora-documents" --since 5m --format short
```

**Verify Aurora cluster is available:**

```bash
aws rds describe-db-clusters \
  --query "DBClusters[?contains(DBClusterIdentifier,'workshop')].{Id:DBClusterIdentifier,Status:Status}" \
  --output table --no-cli-pager
```

**Test RDS Data API directly (as admin):**

```bash
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "${AURORA_DB_NAME:-saasdb}" \
  --sql "SELECT count(*) FROM documents" --no-cli-pager
```

**Verify RLS policy exists:**

```bash
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "${AURORA_DB_NAME:-saasdb}" \
  --sql "SELECT polname, polcmd::text, pg_get_expr(polqual, polrelid) as using_expr FROM pg_policy WHERE polrelid = 'documents'::regclass" \
  --no-cli-pager
```

**Verify app_user role exists:**

```bash
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "${AURORA_DB_NAME:-saasdb}" \
  --sql "SELECT rolname FROM pg_roles WHERE rolname = 'app_user'" --no-cli-pager
```

**Check AppUserSecretArn from CFN outputs:**

```bash
aws cloudformation describe-stacks \
  --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='AppUserSecretArn'].OutputValue" \
  --output text --no-cli-pager
```

**Test the API directly:**

```bash
# Create a document
curl -s -X POST "$API_GATEWAY_URL/aurora/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "aurora-001", "title": "Debug Test", "content": "Testing RLS"}'

# List documents
curl -s "$API_GATEWAY_URL/aurora/documents" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Lab 5: OpenSearch Serverless

**Check Lambda logs:**

```bash
aws logs tail "/aws/lambda/workshop-infrastructure-opensearch-documents" --since 5m --format short
```

**Verify OpenSearch collection is active:**

```bash
aws opensearchserverless list-collections \
  --query "collectionSummaries[].{Name:name,Status:status}" \
  --output table --no-cli-pager
```

**Verify data access policy allows the TVM role:**

```bash
aws opensearchserverless get-access-policy \
  --name saas-vectors-access --type data --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
policy = data['accessPolicyDetail']['policy']
if isinstance(policy, str):
    policy = json.loads(policy)
for rule in policy:
    print(json.dumps(rule, indent=2))
"
```

**Test index + search directly:**

```bash
TOKEN1=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=$TEST_USERNAME1,PASSWORD=$TEST_PASSWORD \
  --query "AuthenticationResult.IdToken" --output text)

# Index a document
curl -s -X POST "$API_GATEWAY_URL/opensearch/documents" \
  -H "Authorization: Bearer $TOKEN1" \
  -H "Content-Type: application/json" \
  -d '{"id": "debug-001", "title": "Debug Test", "content": "searchable content"}'

# Wait for indexing (OpenSearch Serverless has 10-30s delay)
echo "Waiting 15s for indexing..."
sleep 15

# Search
curl -s "$API_GATEWAY_URL/opensearch/documents?q=searchable" \
  -H "Authorization: Bearer $TOKEN1" | python3 -m json.tool
```

**Check if the index exists (via Lambda invocation):**

```bash
# If search returns empty, the index may not exist yet.
# The first document indexed creates the index automatically.
# Wait 15-30 seconds after the first POST before searching.
```

### General Debugging

**Check all CloudFormation outputs (environment variables come from here):**

```bash
aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}" \
  --output table --no-cli-pager
```

**Verify API Gateway deployment:**

```bash
curl -s "$API_GATEWAY_URL/resource" -H "Authorization: Bearer $TOKEN"
```

> If this returns `{"message": "Unauthorized"}`, the authorizer Lambda is denying. Check the authorizer logs above.

> If this returns `{"message": "Internal server error"}`, the downstream Lambda is failing. Check the specific lab's Lambda logs.

**Lambda cold start timeouts:**

Module 1 Lambdas have a 10-30 second cold start (especially OpenSearch and Aurora which establish network connections). If you see timeouts on first invocation, retry after 30 seconds.
