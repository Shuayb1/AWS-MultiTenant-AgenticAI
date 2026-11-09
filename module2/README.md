# Module 2: Data Foundations for Agentic AI — Code

## Directory Structure

```
module2/
├── requirements.txt
├── setup_env.sh                           # Load env vars from CloudFormation
├── 01-lab1-multi-tenant-vector-stores/
│   ├── ingest.py                          # Lab 1: Chunk, embed, ingest into 3 stores (Tasks 1.1–1.4)
│   ├── setup_vector_stores.py             # Create vector stores + upload sample docs (prerequisite)
│   ├── deploy.sh                          # Package + deploy ingestion Lambda
│   └── sample_docs/                       # Per-tenant sample documents
├── 02-lab2-function-calling-tool/
│   └── tool_postgresql.py                 # Lab 2: Aurora pgvector + RLS retrieval tool (Tasks 2.1–2.2)
├── 03-lab3-knowledge-base-tool/
│   ├── tool_opensearch.py                 # Lab 3: Bedrock KB retrieval tool (Tasks 3.1–3.2)
│   └── setup_knowledge_base.py            # Create + sync Bedrock Knowledge Base (prerequisite)
├── 04-lab4-mcp-server-tool/
│   ├── tool_s3_vectors.py                 # Lab 4: S3 Vectors MCP server tool (Tasks 4.1–4.2)
│   └── verify_mcp.py                      # MCP protocol verification client
└── 05-lab5-agentic-tool-selection/
    ├── agent.py                           # Lab 5: Multi-tool Strands Agent Lambda (Tasks 5.1–5.2)
    └── deploy.sh                          # Package + deploy agent Lambda
```

## Lab → Code Directory Mapping

| Lab | Code Directory |
|-----|---------------|
| Lab 1: Embeddings & Ingestion | `01-lab1-multi-tenant-vector-stores/` |
| Lab 2: Function Calling Tool | `02-lab2-function-calling-tool/` |
| Lab 3: Knowledge Base Tool | `03-lab3-knowledge-base-tool/` |
| Lab 4: MCP Server Tool | `04-lab4-mcp-server-tool/` |
| Lab 5: Agentic Tool Selection | `05-lab5-agentic-tool-selection/` |

## Quick Start

```bash
# Setup environment
source setup_env.sh

# Lab 1 — Embeddings & Ingestion
cd 01-lab1-multi-tenant-vector-stores
python setup_vector_stores.py   # prerequisite: create stores + upload sample docs
# Complete Tasks 1.1–1.2 in ingest.py
bash deploy.sh                  # Task 1.3: deploy ingestion Lambda
# Task 1.4: invoke Lambda to ingest documents

# Lab 2 — Function Calling Tool (Aurora pgvector + RLS)
cd ../02-lab2-function-calling-tool
# Complete Tasks 2.1–2.2 in tool_postgresql.py
python tool_postgresql.py       # verify tenant-isolated retrieval

# Lab 3 — Knowledge Base Tool (Bedrock KB + metadata filter)
cd ../03-lab3-knowledge-base-tool
python setup_knowledge_base.py  # prerequisite: create + sync KB
source .kb_env
# Complete Tasks 3.1–3.2 in tool_opensearch.py
python tool_opensearch.py       # verify tenant-isolated retrieval

# Lab 4 — MCP Server Tool (S3 Vectors + per-tenant index)
cd ../04-lab4-mcp-server-tool
# Complete Tasks 4.1–4.2 in tool_s3_vectors.py
python tool_s3_vectors.py --serve  # Terminal 1: start MCP server
python verify_mcp.py               # Terminal 2: verify MCP protocol

# Lab 5 — Agentic Tool Selection (multi-tool agent as Lambda)
cd ../05-lab5-agentic-tool-selection
# Complete Tasks 5.1–5.2 in agent.py
bash deploy.sh                  # deploy agent Lambda
```

## Dependencies

```bash
pip install -r requirements.txt
```

---

## Debugging Guide

Use the commands below to diagnose failures in each lab. Always start by loading environment variables:

```bash
source /workshop/module2/setup_env.sh
```

### Lab 1: Embeddings & Ingestion

**Check if vector stores were created:**

```bash
# S3 Vectors bucket
aws s3vectors list-vector-buckets --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
for b in data.get('vectorBuckets', []):
    print(f\"  {b['vectorBucketName']}\")
"

# S3 Vectors indexes
BUCKET_NAME=$(aws s3vectors list-vector-buckets --no-cli-pager \
  --query "vectorBuckets[?contains(vectorBucketName,'workshop')].vectorBucketName" --output text)
aws s3vectors list-indexes --vector-bucket-name "$BUCKET_NAME" --no-cli-pager
```

**Check if sample documents were uploaded to S3:**

```bash
aws s3 ls "s3://${DATA_BUCKET}/kb-datasource/" --recursive | head -20
```

**Check ingestion Lambda logs:**

```bash
# Function name comes from CFN (may vary)
INGEST_FN=$(aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='IngestFunctionName'].OutputValue | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "workshop-ingest")
aws logs tail "/aws/lambda/$INGEST_FN" --since 5m --format short
```

**Verify embeddings model access:**

```bash
aws bedrock-runtime invoke-model \
  --model-id amazon.nova-2-multimodal-embeddings-v1:0 \
  --body '{"taskType":"SINGLE_EMBEDDING","singleEmbeddingParams":{"embeddingPurpose":"GENERIC_INDEX","embeddingDimension":1024,"text":{"truncationMode":"END","value":"test"}}}' \
  --content-type application/json --accept application/json \
  --cli-binary-format raw-in-base64-out \
  /tmp/embed-test.json --no-cli-pager && echo "Embeddings model OK" || echo "Embeddings model DENIED"
```

### Lab 2: Function Calling Tool (Aurora pgvector + RLS)

**Check Aurora cluster status:**

```bash
aws rds describe-db-clusters \
  --query "DBClusters[?contains(DBClusterIdentifier,'workshop')].{Id:DBClusterIdentifier,Status:Status}" \
  --output table --no-cli-pager
```

**Verify property_surveys table exists and has data:**

```bash
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "${AURORA_DB_NAME:-saasdb}" \
  --sql "SELECT count(*) as total FROM property_mgmt.property_surveys" --no-cli-pager
```

**Check RLS policy on property_surveys:**

```bash
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "${AURORA_DB_NAME:-saasdb}" \
  --sql "SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy WHERE polrelid = 'property_mgmt.property_surveys'::regclass" \
  --no-cli-pager
```

**Test RLS session variable (as admin):**

```bash
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "${AURORA_DB_NAME:-saasdb}" \
  --sql "SELECT set_config('app.current_tenant', 'tenant-001', false)" --no-cli-pager
```

**Check AppUserSecretArn (needed for RLS connection):**

```bash
aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='AppUserSecretArn'].OutputValue" \
  --output text --no-cli-pager
```

### Lab 3: Knowledge Base Tool (Bedrock KB)

**Check if Knowledge Base exists:**

```bash
aws bedrock-agent list-knowledge-bases --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
for kb in data.get('knowledgeBaseSummaries', []):
    print(f\"  {kb['name']} (ID: {kb['knowledgeBaseId']}, Status: {kb['status']})\")
"
```

**Check KB sync status (data source ingestion):**

```bash
source .kb_env 2>/dev/null
# Note: requires bedrock:ListIngestionJobs permission. If AccessDenied,
# check via console: Bedrock > Knowledge Bases > select KB > Data sources
aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --data-source-id "$DATA_SOURCE_ID" \
  --no-cli-pager | python3 -c "
import sys, json
data = json.load(sys.stdin)
for job in data.get('ingestionJobSummaries', []):
    print(f\"  Status: {job['status']} | Documents: {job.get('statistics', {})}\")
" 2>/dev/null || echo "  (AccessDenied - check via Bedrock console instead)"
```

**Test KB retrieval directly:**

```bash
source /workshop/module2/03-lab3-knowledge-base-tool/.kb_env 2>/dev/null
if [ -z "$KNOWLEDGE_BASE_ID" ]; then
  echo "KNOWLEDGE_BASE_ID not set. Run: cd /workshop/module2/03-lab3-knowledge-base-tool && python setup_knowledge_base.py"
else
  aws bedrock-agent-runtime retrieve \
    --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
    --retrieval-query '{"text": "property condition"}' \
    --retrieval-configuration '{"vectorSearchConfiguration":{"filter":{"stringContains":{"key":"x-amz-bedrock-kb-source-uri","value":"/kb-datasource/tenant-001/"}}}}' \
    --no-cli-pager | python3 -m json.tool
fi
```

**Check if .kb_env file was created:**

```bash
cat .kb_env
```

> If missing, re-run `python setup_knowledge_base.py`.

### Lab 4: MCP Server Tool (S3 Vectors)

**Check S3 Vectors indexes:**

```bash
BUCKET_NAME=$(aws s3vectors list-vector-buckets --no-cli-pager \
  --query "vectorBuckets[?contains(vectorBucketName,'workshop')].vectorBucketName" --output text)
aws s3vectors list-indexes --vector-bucket-name "$BUCKET_NAME" --no-cli-pager
```

**Verify data exists in a tenant's index:**

```bash
# Module 2 Lab 4 uses the data bucket, not the memory bucket
DATA_BUCKET=$(aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue | [0]" \
  --output text --no-cli-pager)
echo "Data bucket: $DATA_BUCKET"

# List indexes in the data bucket
aws s3vectors list-indexes --vector-bucket-name "$DATA_BUCKET" --no-cli-pager 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for idx in data.get('indexes', []):
    print(f\"  {idx['indexName']}\")
" 2>/dev/null || echo "  (No S3 Vector indexes found in data bucket - run setup_vector_stores.py first)"
```

**Test MCP server locally:**

```bash
# Start the server in one terminal
python tool_s3_vectors.py --serve &
sleep 2

# Test health endpoint
curl -s http://localhost:9100/health

# Test MCP tools/list
curl -s -X POST http://localhost:9100/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}' | python3 -m json.tool

# Kill the background server
kill %1
```

**Check if port 9100 is in use:**

```bash
lsof -i :9100 2>/dev/null || echo "Port 9100 is free"
```

### Lab 5: Agentic Tool Selection

**Check agent Lambda logs:**

```bash
AGENT_FN=$(aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='AgentFunctionName'].OutputValue | [0]" \
  --output text --no-cli-pager 2>/dev/null || echo "workshop-module2-agent")
aws logs tail "/aws/lambda/$AGENT_FN" --since 5m --format short
```

**Verify all 3 retrieval tools work independently:**

```bash
cd /workshop/module2/02-lab2-function-calling-tool && python tool_postgresql.py
cd /workshop/module2/03-lab3-knowledge-base-tool && python tool_opensearch.py
cd /workshop/module2/04-lab4-mcp-server-tool && python tool_s3_vectors.py --test
```

**Check Bedrock model access (Nova Pro):**

```bash
aws bedrock-runtime converse \
  --model-id amazon.nova-pro-v1:0 \
  --messages '[{"role":"user","content":[{"text":"hello"}]}]' \
  --no-cli-pager --query "output.message.content[0].text" --output text \
  && echo "Model access OK" || echo "Model access DENIED"
```

**Test the deployed agent via API:**

```bash
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $COGNITO_CLIENT_ID \
  --auth-parameters USERNAME=$TEST_USERNAME1,PASSWORD=$TEST_PASSWORD \
  --query 'AuthenticationResult.IdToken' --output text)

curl -s -X POST "$API_GATEWAY_URL/agent" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the condition of the roof at my property?"}' | python3 -m json.tool
```

### General Debugging

**Check all CloudFormation outputs:**

```bash
aws cloudformation describe-stacks --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}" \
  --output table --no-cli-pager
```

**Verify Bedrock model access (common failure):**

```bash
aws bedrock list-foundation-models \
  --query "modelSummaries[?modelId=='amazon.nova-pro-v1:0'].{Id:modelId,Status:modelLifecycle.status}" \
  --output table --no-cli-pager
```

**Check TVM role permissions:**

```bash
aws iam get-role-policy \
  --role-name workshop-infrastructure-tenant-isolation-role \
  --policy-name TenantIsolationPolicy --no-cli-pager | python3 -m json.tool
```

**Common issues:**

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `NotImplementedError` | TODO stub not completed | Complete the task in the code |
| `AccessDeniedException` on STS | TVM role trust policy missing caller | Check role trust allows the calling role |
| `AccessDeniedException` on Bedrock | Model not enabled in region | Enable the model in Bedrock console |
| Empty retrieval results | Data not ingested | Re-run the ingestion/setup script |
| `index_not_found_exception` | S3 Vectors index not created | Run `setup_vector_stores.py` |
| KB retrieval empty | Sync not complete | Wait for ingestion job to finish |
