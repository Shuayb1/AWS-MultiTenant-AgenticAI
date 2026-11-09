# Module 3, Lab 2: Multi-Tenant MCP Tool Gateway

## How It Works

The lab deploys a multi-tenant MCP Tool Gateway with two target types:

1. **Lambda target** (`query_orders`) - DynamoDB, tenant isolation via interceptor-injected tenant_id + TVM credentials
2. **MCP Server target** (`query_properties`) - Aurora PostgreSQL, tenant isolation via per-tenant OAuth token from AgentCore Identity + RLS

### Architecture Flow

```
User JWT --> Gateway (Cognito auth + Cedar policy)
                |
                +--> Interceptor Lambda (extract tenant_id, inject into args, audit log)
                |
                +--> [Lambda target] query_orders
                |       Uses TVM to get tenant-scoped DynamoDB credentials
                |
                +--> [MCP Server target] query_properties
                        Gateway fetches OAuth token from credential provider
                        MCP Server validates token, sets RLS context, queries Aurora
```

### Key Components

| Component | Purpose |
|-----------|---------|
| Gateway | Single MCP endpoint for all tenants, JWT auth + Cedar policy |
| Interceptor | Extracts tenant_id from JWT, injects into tool args, audit trail |
| Cedar Policy | Hard boundary - denies requests without `custom:tenant_id` claim |
| Credential Provider | OAuth2 (Client Credentials) via Cognito for MCP Server auth |
| query_orders Lambda | DynamoDB tool with TVM-scoped credentials |
| query_properties MCP Server | ECS Fargate Flask app, Aurora RLS per tenant |

### Gateway Execution Role Permissions

The gateway exec role needs these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["lambda:InvokeFunction"],
      "Resource": "arn:aws:lambda:{region}:{account}:function:workshop-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetResourceOauth2Token",
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "*"
    }
  ]
}
```

### MCP Server Requirements

- Must handle `GET /mcp` (returns 200) for MCP protocol 2025-06-18 SSE compatibility
- Must handle `POST /mcp` for JSON-RPC (initialize, tools/list, tools/call)
- `EXPECTED_AUDIENCE` env var should match the public Cognito client ID (for user JWT validation fallback)
- Reads `tenant_id` from tool arguments (injected by interceptor) as primary source

---

## Deployment Order

```bash
cd /workshop/module3
source setup_env.sh
cd 02-lab2-multi-tenant-mcp-tool-gateway

# 1. Deploy Lambda tools
bash deploy_query_orders.sh

# 2. Deploy MCP Server (ECS Fargate + CloudFront)
bash deploy_query_properties_mcp_server.sh

# 3. Set up OAuth credential provider
bash setup_credential_provider.sh

# 4. Deploy interceptor + Gateway
bash deploy_gateway.sh

# 5. Deploy agent
bash deploy.sh
```

---

## Troubleshooting

### 1. Gateway returns "An internal error occurred"

This is the Gateway's generic error. Check CloudWatch Logs for the Gateway:

```bash
# Gateway logs (available via CloudWatch Logs Insights)
# Log group: /aws/bedrock-agentcore/gateway/{gateway-id}
# Or check via the AgentCore console
```

Common causes:
- **Missing IAM permissions** on gateway exec role (see above)
- **OAuth credential provider misconfigured** (wrong client ID/secret)
- **MCP Server returning 405 on GET /mcp** (protocol version mismatch)

### 2. Check MCP Server directly (bypass Gateway)

```bash
# Get endpoint
MCP_ENDPOINT=$(aws bedrock-agentcore-control get-gateway-target \
  --gateway-id "$GATEWAY_ID" \
  --target-id $(aws bedrock-agentcore-control list-gateway-targets --gateway-id "$GATEWAY_ID" --no-cli-pager \
    --query "items[?name=='query-properties'].targetId" --output text) \
  --no-cli-pager --query "targetConfiguration.mcp.mcpServer.endpoint" --output text)

# Test tools/list
curl -s -X POST "$MCP_ENDPOINT" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}' | python3 -m json.tool

# Test tools/call (with tenant_id in args - no auth needed)
curl -s -X POST "$MCP_ENDPOINT" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"query_properties","arguments":{"tenant_id":"tenant-001","limit":5}},"id":1}' | python3 -m json.tool

# Test GET /mcp (must return 200 for protocol compatibility)
curl -s -o /dev/null -w "%{http_code}" -X GET "$MCP_ENDPOINT"
```

### 3. Check OAuth token flow manually

```bash
# Get client credentials
GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoGatewayClientId'].OutputValue" --output text --no-cli-pager)
CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$COGNITO_USER_POOL_ID" --client-id "$GATEWAY_CLIENT_ID" \
  --query "UserPoolClient.ClientSecret" --output text --no-cli-pager)
COGNITO_DOMAIN=$(aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoDomain'].OutputValue" --output text --no-cli-pager)

# Fetch token
curl -s -X POST "${COGNITO_DOMAIN}/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -u "${GATEWAY_CLIENT_ID}:${CLIENT_SECRET}" \
  -d "grant_type=client_credentials&scope=mcp-server/query" | python3 -m json.tool
```

If this fails: Cognito client missing secret, wrong scope, or no domain configured.

### 4. Check credential provider status

```bash
aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name "workshop-properties-provider" --no-cli-pager | python3 -m json.tool
```

Verify:
- `status: READY`
- `clientId` matches the gateway Cognito client
- `discoveryUrl` points to the correct Cognito pool

### 5. Aurora RLS issues (empty results or auth failures)

```bash
# Check app_user exists
aws rds-data execute-statement \
  --resource-arn "$CLUSTER_ARN" --secret-arn "$SECRET_ARN" --database saasdb \
  --sql "SELECT 1 FROM pg_roles WHERE rolname='app_user'" --no-cli-pager

# Check RLS policy exists
aws rds-data execute-statement \
  --resource-arn "$CLUSTER_ARN" --secret-arn "$SECRET_ARN" --database saasdb \
  --sql "SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy WHERE polrelid = 'properties'::regclass" --no-cli-pager

# Test RLS as app_user (must use transaction for SET to persist)
APP_USER_SECRET=$(aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='AppUserSecretArn'].OutputValue" --output text --no-cli-pager)

TXN_ID=$(aws rds-data begin-transaction \
  --resource-arn "$CLUSTER_ARN" --secret-arn "$APP_USER_SECRET" --database saasdb \
  --query "transactionId" --output text --no-cli-pager)

aws rds-data execute-statement \
  --resource-arn "$CLUSTER_ARN" --secret-arn "$APP_USER_SECRET" --database saasdb \
  --transaction-id "$TXN_ID" --sql "SET app.tenant_id = 'tenant-001'" --no-cli-pager

aws rds-data execute-statement \
  --resource-arn "$CLUSTER_ARN" --secret-arn "$APP_USER_SECRET" --database saasdb \
  --transaction-id "$TXN_ID" --sql "SELECT * FROM properties" --no-cli-pager

aws rds-data commit-transaction \
  --resource-arn "$CLUSTER_ARN" --secret-arn "$APP_USER_SECRET" \
  --transaction-id "$TXN_ID" --no-cli-pager
```

Key points:
- RLS policy must use `current_setting('app.tenant_id', true)` (the `true` prevents errors when unset)
- `app_user` must NOT be the table owner (owners bypass RLS)
- Data must be queried within a transaction (SET + SELECT in same transaction)

### 6. Check interceptor logs

```bash
aws logs tail "/aws/lambda/workshop-gateway-interceptor" --since 5m --format short
```

Should show `tenant=tenant-001 method=tools/call` for each invocation.

### 7. Check MCP Server container logs

```bash
aws logs tail "/ecs/workshop-infrastructure-mcp-server" --since 5m --format short | grep -v health
```

### 8. Gateway target status

```bash
aws bedrock-agentcore-control list-gateway-targets --gateway-id "$GATEWAY_ID" --no-cli-pager | python3 -m json.tool
```

Both targets should be `READY`.

---

## Common Issues and Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| "An internal error occurred" | Gateway can't get OAuth token | Add IAM permissions to gateway exec role |
| MCP Server 405 on GET /mcp | Missing GET handler | Add `GET` to `@app.route("/mcp", methods=["POST", "GET"])` |
| "password authentication failed for user app_user" | app_user not created in Aurora | Run `source setup_env.sh` (creates app_user + RLS) |
| Empty properties [] | RLS filtering all rows | Check policy uses `current_setting('app.tenant_id', true)`, verify data exists |
| "JWT missing custom:tenant_id claim" | Token from user without tenant attribute | Expected for Cedar denial test (notenant@example.com) |
| Credential provider "already exists" | Script skips creation | Script now updates if exists; or delete from console and re-run |

---

## Cleanup

```bash
# Delete Gateway targets
for TARGET_ID in $(aws bedrock-agentcore-control list-gateway-targets --gateway-id "$GATEWAY_ID" --no-cli-pager --query "items[].targetId" --output text); do
  aws bedrock-agentcore-control delete-gateway-target --gateway-id "$GATEWAY_ID" --target-id "$TARGET_ID" --no-cli-pager 2>/dev/null || true
done

# Delete Gateway
aws bedrock-agentcore-control delete-gateway --gateway-id "$GATEWAY_ID" --no-cli-pager 2>/dev/null || true

# Delete credential provider
aws bedrock-agentcore-control delete-oauth2-credential-provider --name "workshop-properties-provider" --no-cli-pager 2>/dev/null || true

# Delete Lambdas
aws lambda delete-function --function-name workshop-gateway-interceptor --no-cli-pager 2>/dev/null || true
aws lambda delete-function --function-name workshop-gateway-query-orders --no-cli-pager 2>/dev/null || true

# Delete agent runtime
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --no-cli-pager --query "agentRuntimes[?contains(agentRuntimeName,'Lab2')].agentRuntimeId" --output text 2>/dev/null)
if [ -n "$RUNTIME_ID" ] && [ "$RUNTIME_ID" != "None" ]; then
  aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" --no-cli-pager 2>/dev/null || true
fi

# Clean local state
rm -f .lab2_env .gateway_env .bedrock_agentcore.yaml
```
