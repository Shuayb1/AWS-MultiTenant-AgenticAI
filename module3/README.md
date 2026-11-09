# Module 3: Multi-Tenant Agentic Patterns — Code

## Directory Structure

```
module3/
├── setup_env.sh                           # Load env vars from CloudFormation
├── 01-lab1-multi-tenant-agents/
│   ├── agent.py                           # Lab 1: Pooled agent on AgentCore Runtime
│   ├── backend_lambda.py                  # Backend Lambda for agent invocation
│   ├── token_vending_machine.py           # TVM for tenant-scoped credentials
│   ├── test_properties.py                 # Verify tenant isolation properties
│   ├── setup_tvm_role.sh                  # Create TVM IAM role (prerequisite)
│   ├── deploy.sh                          # Deploy agent to AgentCore
│   ├── deploy_backend.sh                  # Deploy backend Lambda
│   └── requirements.txt
├── 02-lab2-multi-tenant-mcp-tool-gateway/
│   ├── agent.py                           # Lab 2: Agent with MCP Gateway tools
│   ├── interceptor.py                     # Gateway interceptor (header propagation + audit)
│   ├── cedar_policy.json                  # Cedar authorization policies
│   ├── query_orders_lambda.py             # DynamoDB order query tool (Lambda target)
│   ├── query_properties_mcp_server.py     # Aurora property query (MCP Server target)
│   ├── token_vending_machine.py           # TVM for tool-scoped credentials
│   ├── setup_credential_provider.sh       # Configure OAuth credential provider
│   ├── deploy.sh                          # Deploy agent to AgentCore
│   ├── deploy_gateway.sh                  # Deploy MCP Gateway + targets
│   ├── deploy_interceptor.sh             # Deploy interceptor Lambda
│   ├── deploy_query_orders.sh            # Deploy orders tool Lambda + seed data
│   ├── deploy_query_properties_mcp_server.sh  # Deploy MCP server (ECS Fargate)
│   └── requirements.txt
├── 03-lab3-agentcore-memory/
│   ├── agent.py                           # Lab 3: Agent with AgentCore Memory
│   ├── agentcore_memory_session_manager.py  # Tenant-isolated session manager
│   ├── isolation_manager.py               # Memory isolation enforcement
│   ├── deploy.sh                          # Deploy agent to AgentCore
│   ├── setup_tvm_role.sh                  # Create TVM IAM role (prerequisite)
│   ├── test_isolation.py                  # Verify memory isolation
│   └── requirements.txt
├── 04-lab4-s3-vector-memory/
│   ├── agent.py                           # Lab 4: Agent with S3 Vectors as memory
│   ├── deploy.sh                          # Deploy agent + create indexes + TVM role
│   ├── setup_tvm_role.sh                  # Create TVM IAM role (prerequisite)
│   ├── test_isolation.py                  # Verify S3 Vector isolation
│   └── requirements.txt
└── 05-lab5-per-tenant-usage-metering/
    ├── agent.py                           # Lab 5: Agent with usage metering
    ├── tenant_metering_plugin.py          # CloudWatch metrics plugin
    ├── enable_observability.py            # Enable CloudWatch observability
    ├── deploy.sh                          # Deploy metering agent
    ├── generate_load.sh                   # Generate multi-tenant load
    ├── view_metrics.py                    # View per-tenant metrics
    └── requirements.txt
```

## Lab → Code Directory Mapping

| Lab | Code Directory |
|-----|---------------|
| Lab 1: Multi-Tenant Agents | `01-lab1-multi-tenant-agents/` |
| Lab 2: Multi-Tenant MCP Tool Gateway | `02-lab2-multi-tenant-mcp-tool-gateway/` |
| Lab 3: AgentCore Memory | `03-lab3-agentcore-memory/` |
| Lab 4: S3 Vectors as Memory | `04-lab4-s3-vector-memory/` |
| Lab 5: Per-Tenant Usage Metering | `05-lab5-per-tenant-usage-metering/` |

## Quick Start

```bash
# Setup environment
source setup_env.sh

# Lab 1 — Multi-Tenant Agents (AgentCore Runtime + TVM)
cd 01-lab1-multi-tenant-agents
bash setup_tvm_role.sh          # prerequisite: create TVM IAM role
# Complete tasks in agent.py
bash deploy.sh                  # deploy agent to AgentCore
bash deploy_backend.sh          # deploy backend Lambda
python test_properties.py       # verify tenant isolation

# Lab 2 — Multi-Tenant MCP Tool Gateway
cd ../02-lab2-multi-tenant-mcp-tool-gateway
bash deploy_query_orders.sh     # deploy Lambda target + seed DynamoDB
bash deploy_query_properties_mcp_server.sh  # deploy MCP Server (ECS)
bash setup_credential_provider.sh           # configure OAuth
# Complete tasks in interceptor.py
bash deploy_interceptor.sh      # deploy interceptor
bash deploy_gateway.sh          # deploy Gateway with targets + Cedar policy
bash deploy.sh                  # deploy agent

# Lab 3 — AgentCore Memory
cd ../03-lab3-agentcore-memory
bash setup_tvm_role.sh
# Complete tasks in agentcore_memory_session_manager.py
bash deploy.sh
python test_isolation.py

# Lab 4 — S3 Vectors as Memory
cd ../04-lab4-s3-vector-memory
bash deploy.sh                  # creates indexes + TVM role + deploys agent
python test_isolation.py

# Lab 5 — Per-Tenant Usage Metering
cd ../05-lab5-per-tenant-usage-metering
# Complete tasks in tenant_metering_plugin.py
bash deploy.sh
bash generate_load.sh           # generate traffic
python view_metrics.py          # view per-tenant metrics
```

## Dependencies

Each lab has its own `requirements.txt`. Install from the lab directory:

```bash
pip install -r requirements.txt
```

---

## Debugging Guide

Use the commands below to diagnose failures in each lab. Always start by loading environment variables:

```bash
source /workshop/module3/setup_env.sh
```

### Lab 1: Multi-Tenant Agents

**Check AgentCore Runtime agent status:**

```bash
aws bedrock-agentcore-control list-agent-runtimes --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('agentRuntimes', []):
    print(f\"  {r.get('agentRuntimeId', 'unknown')} - Status: {r.get('status', 'unknown')}\")
"
```

**Check agent logs (AgentCore Runtime):**

```bash
# Find the runtime ID (Lab 1 agent is named "PooledAgent" or "Lab1")
source .agent_env 2>/dev/null
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --no-cli-pager \
  --query "agentRuntimes[?contains(agentRuntimeId,'PooledAgent') || contains(agentRuntimeId,'Lab1')].agentRuntimeId | [0]" --output text)
echo "Runtime: $RUNTIME_ID"
aws logs tail "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT" --since 5m --format short
```

**Check backend Lambda logs:**

```bash
aws logs tail "/aws/lambda/workshop-module3-lab1-backend" --since 5m --format short
```

**Test TVM role (AssumeRole with session tag):**

```bash
aws sts assume-role \
  --role-arn "$TENANT_ISOLATION_ROLE_ARN" \
  --role-session-name "debug-tenant-001" \
  --tags Key=TenantID,Value=tenant-001 \
  --query "Credentials.AccessKeyId" --output text && echo "TVM AssumeRole OK"
```

**Verify agent endpoint is reachable:**

```bash
source .agent_env 2>/dev/null
curl -s -X POST "$AGENT_ENDPOINT" \
  -H "Authorization: Bearer $TOKEN_TENANT_1" \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: debug-tenant-001-session-000000001" \
  -d '{"message": "hello"}' | python3 -m json.tool
```

**Check DynamoDB has seed data (orders for testing):**

```bash
aws dynamodb query --table-name SaaSApplicationData \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values '{":pk":{"S":"tenant-001#orders"}}' \
  --query "Count" --output text
```

### Lab 2: Multi-Tenant MCP Tool Gateway

**Check interceptor Lambda logs:**

```bash
aws logs tail "/aws/lambda/workshop-gateway-interceptor" --since 5m --format short
```

**Check query_orders Lambda logs:**

```bash
aws logs tail "/aws/lambda/workshop-query-orders" --since 5m --format short
```

**Verify Gateway exists and targets have header propagation configured:**

```bash
source .lab2_env 2>/dev/null
GATEWAY_ID=$(echo "$GATEWAY_ENDPOINT" | sed 's|.*/\(workshopmultitenanttoolgateway-[a-z0-9]*\).*|\1|')
echo "Gateway: $GATEWAY_ID"

# Get target IDs
TARGETS=$(aws bedrock-agentcore-control list-gateway-targets \
  --gateway-identifier "$GATEWAY_ID" --no-cli-pager)
echo "$TARGETS" | python3 -c "import sys,json; [print(f'  {t[\"name\"]} (ID: {t[\"targetId\"]})') for t in json.load(sys.stdin).get('items',[])]"

# Check each target's header configuration
for TARGET_ID in $(echo "$TARGETS" | python3 -c "import sys,json; [print(t['targetId']) for t in json.load(sys.stdin).get('items',[])]"); do
  echo ""
  aws bedrock-agentcore-control get-gateway-target \
    --gateway-identifier "$GATEWAY_ID" \
    --target-id "$TARGET_ID" \
    --no-cli-pager --query "{name:name,headers:metadataConfiguration.allowedRequestHeaders}" --output json
done
```

> If `allowedRequestHeaders` shows `NOT SET`, redeploy the gateway: `bash deploy_gateway.sh`

**Test Gateway directly (bypassing agent):**

```bash
# tools/list
curl -s -X POST "$GATEWAY_ENDPOINT" \
  -H "Authorization: Bearer $TOKEN_TENANT_1" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}' | python3 -m json.tool

# tools/call (query_orders)
curl -s -X POST "$GATEWAY_ENDPOINT" \
  -H "Authorization: Bearer $TOKEN_TENANT_1" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"query-orders___query_orders","arguments":{"limit":5}},"id":1}' | python3 -m json.tool

# tools/call (query_properties)
curl -s -X POST "$GATEWAY_ENDPOINT" \
  -H "Authorization: Bearer $TOKEN_TENANT_1" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"query-properties___query_properties","arguments":{"limit":5}},"id":1}' | python3 -m json.tool
```

**Test MCP Server directly (bypassing Gateway):**

```bash
MCP_ENDPOINT=$(aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='McpServerAlbEndpoint'].OutputValue" \
  --output text --no-cli-pager)

# Health check
curl -s "$MCP_ENDPOINT/health"

# tools/call with user token
curl -s -X POST "$MCP_ENDPOINT/mcp" \
  -H "Content-Type: application/json" \
  -H "x-user-token: $TOKEN_TENANT_1" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"query_properties","arguments":{"limit":5}},"id":1}' | python3 -m json.tool
```

**Check Cedar policy denies users without tenant_id:**

```bash
TOKEN_NO_TENANT=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=notenant@example.com,PASSWORD=$TEST_PASSWORD \
  --query 'AuthenticationResult.IdToken' --output text)

curl -s -X POST "$GATEWAY_ENDPOINT" \
  -H "Authorization: Bearer $TOKEN_NO_TENANT" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"query-orders___query_orders","arguments":{}},"id":1}'
```

> Expected: error with "JWT missing custom:tenant_id claim"

**Check credential provider (OAuth for MCP Server):**

```bash
aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name "workshop-properties-provider" --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f\"  Status: {data.get('status', 'unknown')}\")
print(f\"  ARN: {data.get('credentialProviderArn', 'N/A')}\")
"
```

**Verify DynamoDB orders are seeded:**

```bash
aws dynamodb query --table-name SaaSApplicationData \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values '{":pk":{"S":"tenant-001#orders"}}' \
  --query "Items[].{SK:SK.S,product:product.S}" --output table --no-cli-pager
```

**Verify Aurora properties are seeded:**

```bash
source /workshop/module1/setup_env.sh 2>/dev/null
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "${AURORA_DB_NAME:-saasdb}" \
  --sql "SELECT tenant_id, count(*) FROM properties GROUP BY tenant_id" --no-cli-pager
```

### Lab 3: AgentCore Memory

**Check agent logs:**

```bash
source .lab3_env 2>/dev/null
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --no-cli-pager \
  --query "agentRuntimes[?contains(agentRuntimeId,'Lab3') || contains(agentRuntimeId,'Memory')].agentRuntimeId | [0]" --output text)
echo "Runtime: $RUNTIME_ID"
aws logs tail "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT" --since 5m --format short
```

**Verify memory resource exists:**

```bash
aws bedrock-agentcore-control list-memories --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('memories', []):
    print(json.dumps({k:v for k,v in m.items() if k in ('memoryId','name','status','memoryArn')}, default=str))
"
```

**Test TVM role for memory access:**

```bash
TVM_ROLE_ARN="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/workshop-infrastructure-lab3-memory-tvm-role"
aws sts assume-role \
  --role-arn "$TVM_ROLE_ARN" \
  --role-session-name "debug-tenant-001" \
  --tags Key=TenantID,Value=tenant-001 \
  --query "Credentials.AccessKeyId" --output text && echo "TVM OK"
```

**Check TVM role policy:**

```bash
aws iam get-role-policy \
  --role-name workshop-infrastructure-lab3-memory-tvm-role \
  --policy-name AgentCoreMemoryTenantPolicy --no-cli-pager | python3 -m json.tool
```

### Lab 4: S3 Vectors as Memory

**Check agent logs:**

```bash
source .lab4_env 2>/dev/null
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --no-cli-pager \
  --query "agentRuntimes[?contains(agentRuntimeId,'Lab4') || contains(agentRuntimeId,'S3Vector')].agentRuntimeId | [0]" --output text)
echo "Runtime: $RUNTIME_ID"
aws logs tail "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT" --since 5m --format short
```

**Verify S3 Vector bucket and indexes exist:**

```bash
BUCKET_NAME=$(aws s3vectors list-vector-buckets --no-cli-pager \
  --query "vectorBuckets[?contains(vectorBucketName,'memory')].vectorBucketName" --output text)
echo "Bucket: $BUCKET_NAME"
aws s3vectors list-indexes --vector-bucket-name "$BUCKET_NAME" --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
for idx in data.get('indexes', []):
    print(f\"  {idx['indexName']} ({idx.get('status','unknown')})\")
"
```

**Check TVM role policy (must include ListVectors):**

```bash
aws iam get-role-policy \
  --role-name workshop-infrastructure-lab4-s3vectors-tvm-role \
  --policy-name S3VectorsTenantPolicy --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
actions = data['PolicyDocument']['Statement'][0]['Action']
print('Actions:', actions)
if 's3vectors:ListVectors' not in actions:
    print('WARNING: ListVectors is MISSING! Redeploy with: bash deploy.sh')
else:
    print('OK: ListVectors present')
"
```

**Test TVM role for S3 Vectors access:**

```bash
TVM_ROLE_ARN="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/workshop-infrastructure-lab4-s3vectors-tvm-role"
aws sts assume-role \
  --role-arn "$TVM_ROLE_ARN" \
  --role-session-name "tenant-tenant-001" \
  --tags Key=TenantID,Value=tenant-001 \
  --query "Credentials.AccessKeyId" --output text && echo "TVM OK"
```

**Run the isolation test:**

```bash
python test_isolation.py
```

> Step 1 should succeed (tenant-001 accesses own index), Step 2 should be denied (cross-tenant blocked by IAM).

### Lab 5: Per-Tenant Usage Metering

**Check agent logs:**

```bash
source .lab5_env 2>/dev/null
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --no-cli-pager \
  --query "agentRuntimes[?contains(agentRuntimeId,'Lab5') || contains(agentRuntimeId,'Metering')].agentRuntimeId | [0]" --output text)
echo "Runtime: $RUNTIME_ID"
aws logs tail "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT" --since 5m --format short
```

**Check CloudWatch metrics are being emitted:**

```bash
aws cloudwatch list-metrics \
  --namespace "SaaS/AgentMetering" \
  --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
metrics = data.get('Metrics', [])
print(f'Metrics found: {len(metrics)}')
for m in metrics[:10]:
    dims = {d['Name']:d['Value'] for d in m['Dimensions']}
    print(f\"  {m['MetricName']} | TenantId={dims.get('TenantId','?')}\")
"
```

**View recent metric data points:**

```bash
python view_metrics.py
```

**Generate test load and check output:**

```bash
bash generate_load.sh
```

**Verify CloudWatch PutMetricData permissions:**

```bash
# Check if the execution role has the CloudWatchMetering policy
EXEC_ROLE=$(aws iam list-roles \
  --query "Roles[?starts_with(RoleName,'AmazonBedrockAgentCoreSDKRuntime-')].RoleName | [0]" \
  --output text --no-cli-pager)
echo "Execution role: $EXEC_ROLE"
aws iam get-role-policy --role-name "$EXEC_ROLE" --policy-name CloudWatchMetering --no-cli-pager 2>/dev/null \
  && echo "CloudWatchMetering policy: ATTACHED" \
  || echo "CloudWatchMetering policy: MISSING (redeploy with: bash deploy.sh)"
```

### General Debugging

**Check all CloudFormation outputs:**

```bash
aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}" \
  --output table --no-cli-pager
```

**Verify Bedrock model access (Nova Pro):**

```bash
aws bedrock-runtime converse \
  --model-id amazon.nova-pro-v1:0 \
  --messages '[{"role":"user","content":[{"text":"hello"}]}]' \
  --no-cli-pager --query "output.message.content[0].text" --output text \
  && echo "Model access OK" || echo "Model access DENIED"
```

**Get fresh tokens for testing:**

```bash
source /workshop/module3/setup_env.sh

TOKEN_TENANT_1=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=$TEST_USERNAME1,PASSWORD=$TEST_PASSWORD \
  --query 'AuthenticationResult.IdToken' --output text)

TOKEN_TENANT_2=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=testuser2@example.com,PASSWORD=$TEST_PASSWORD \
  --query 'AuthenticationResult.IdToken' --output text)

echo "Tokens ready: TOKEN_TENANT_1, TOKEN_TENANT_2"
```

**Common issues:**

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `NotImplementedError` | TODO stub not completed | Complete the task in the code |
| `AccessDeniedException` on STS | TVM role trust or tag missing | Run `setup_tvm_role.sh` or check trust policy |
| `AccessDeniedException` on s3vectors | Policy missing action | Redeploy: `bash deploy.sh` (updates IAM policy) |
| `x-tenant-id header missing` | Gateway targets missing allowedRequestHeaders | Redeploy: `bash deploy_gateway.sh` |
| MCP Server returns error via Gateway | OAuth credential provider not configured | Run `bash setup_credential_provider.sh` |
| Empty orders/properties from Gateway | Seed data not loaded | Run `bash deploy_query_orders.sh` (seeds DynamoDB) |
| Agent returns "tenant_id missing" | JWT not forwarded to agent | Check session ID header is unique per request |
| Memory isolation test fails step 1 | ListVectors missing from TVM policy | Redeploy: `bash deploy.sh` |
| CloudWatch metrics empty | Agent not invoked or PutMetricData denied | Run `bash generate_load.sh`, check execution role |
