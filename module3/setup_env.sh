#!/usr/bin/env bash
# setup_env.sh — Resolve infrastructure from workshop-infrastructure stack
# for Module 3 labs.
#
# Usage:
#   cd /workshop/module3
#   source setup_env.sh

STACK_NAME="${STACK_NAME:-workshop-infrastructure}"
export AWS_REGION="${AWS_REGION:-us-east-1}"

echo "Resolving infrastructure from stack: $STACK_NAME ..."

_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text --no-cli-pager
}

# ── Core ──────────────────────────────────────────────────────────────────
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)

# ── Cognito (JWT generation) ──────────────────────────────────────────────
export COGNITO_USER_POOL_ID=$(_output CognitoUserPoolId)
export COGNITO_CLIENT_ID=$(_output CognitoClientId)

# ── DynamoDB ──────────────────────────────────────────────────────────────
export TENANT_METADATA_TABLE=$(_output TenantMetadataTableName)
export CRM_TABLE_NAME=$(_output SaaSApplicationDataTableName)

# ── S3 ────────────────────────────────────────────────────────────────────
export S3_BUCKET_NAME=$(_output DataBucketName)

# ── TVM Role (tenant-scoped credentials) ──────────────────────────────────
export TVM_ROLE_ARN=$(_output TenantIsolationRoleArn)
export AGENTCORE_TVM_ROLE_ARN="$TVM_ROLE_ARN"

# ── AgentCore Memory (Lab 3/4) ────────────────────────────────────────────
export AGENTCORE_MEMORY_ROLE_ARN=$(_output AgentCoreMemoryExecutionRoleArn)

# ── AgentCore Gateway (from Lab 2 — discovered or set manually) ───
if [ -z "${GATEWAY_ID:-}" ]; then
  GATEWAY_ID=$(aws bedrock-agentcore-control list-gateways --region "$AWS_REGION" --no-cli-pager \
    --query "items[0].gatewayId" --output text 2>/dev/null || echo "")
fi
export GATEWAY_ID="${GATEWAY_ID:-}"

# ── Test user credentials ─────────────────────────────────────────────────
export TEST_USER_EMAIL="${TEST_USER_EMAIL:-testuser1@example.com}"
export TEST_USER_PASSWORD="${TEST_USER_PASSWORD:-Workshop@123!}"
export TEST_PASSWORD="$TEST_USER_PASSWORD"
export TEST_USER2_EMAIL="${TEST_USER2_EMAIL:-testuser2@example.com}"
export TEST_USER2_PASSWORD="${TEST_USER2_PASSWORD:-Workshop@123!}"

# ── API Gateway ───────────────────────────────────────────────────────────
export API_GATEWAY_URL=$(_output ApiGatewayUrl)

# ── Deploy Lambda Authorizer (if not already deployed) ─────────────────────
# The API Gateway Lambda authorizer is needed for Phase 2 of Lab 1.
# This ensures Module 3 can run independently of Module 1.
AUTHORIZER_FUNCTION_NAME=$(_output AuthorizerFunctionName)
if [ -n "$AUTHORIZER_FUNCTION_NAME" ]; then
  _SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _AUTH_SRC="${_SCRIPT_DIR}/../module1/01-lab1-saas-identity"
  if [ -f "${_AUTH_SRC}/authorizer.py" ] && [ -f "${_AUTH_SRC}/saas_identity.py" ]; then
    echo "→ Ensuring Lambda Authorizer is deployed..."
    _AUTH_PKG=$(mktemp -d)
    pip install PyJWT -t "$_AUTH_PKG" --quiet 2>/dev/null
    cp "${_AUTH_SRC}/authorizer.py" "$_AUTH_PKG/index.py"
    cp "${_AUTH_SRC}/saas_identity.py" "$_AUTH_PKG/"

    # Patch saas_identity.py stubs with solutions (so Module 3 works independently)
    python3 -c "
import re
path = '$_AUTH_PKG/saas_identity.py'
with open(path) as f:
    content = f.read()

# Task 1.1: extract_tenant_id
content = content.replace(
    '        raise NotImplementedError(\"Complete Task 1.1\")  # REPLACE THIS LINE',
    '        return decoded_token.get(\"custom:tenant_id\")')

# Task 1.2: get_tenant_context
content = content.replace(
    '        raise NotImplementedError(\"Complete Task 1.2\")  # REPLACE THIS LINE',
    '''        try:
            response = self.table.get_item(Key={\"tenantId\": tenant_id})
            return response.get(\"Item\")
        except Exception as e:
            print(f\"DynamoDB error: {e}\")
            return None''')

# Task 1.3: authorize_request
content = content.replace(
    '        raise NotImplementedError(\"Complete Task 1.3\")  # REPLACE THIS LINE',
    '''        tenant_id = self.extract_tenant_id(decoded_token)
        if not tenant_id:
            return None
        context = self.get_tenant_context(tenant_id)
        if not context or context.get(\"status\") != \"active\":
            return None
        return context''')

with open(path, 'w') as f:
    f.write(content)
"

    (cd "$_AUTH_PKG" && zip -r /tmp/authorizer-deploy.zip . --quiet)
    aws lambda update-function-code \
      --function-name "$AUTHORIZER_FUNCTION_NAME" \
      --zip-file fileb:///tmp/authorizer-deploy.zip \
      --no-cli-pager > /dev/null 2>&1
    rm -rf "$_AUTH_PKG" /tmp/authorizer-deploy.zip
    echo "  Authorizer deployed: $AUTHORIZER_FUNCTION_NAME"

    # Seed TenantMetadata table if empty
    _ITEM_COUNT=$(aws dynamodb scan --table-name TenantMetadata --select COUNT \
      --query "Count" --output text --no-cli-pager 2>/dev/null || echo "0")
    if [ "$_ITEM_COUNT" = "0" ]; then
      echo "  Seeding TenantMetadata table..."
      python3 "${_AUTH_SRC}/setup_dynamodb.py" 2>/dev/null || true
    fi
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "✓ Environment configured for Module 3"
echo "  AGENTCORE_TVM_ROLE_ARN  = ${AGENTCORE_TVM_ROLE_ARN:-<not found>}"
echo "  GATEWAY_ID              = ${GATEWAY_ID:-<not found — set manually or deploy Lab 2 first>}"
echo "  S3_BUCKET_NAME          = ${S3_BUCKET_NAME:-<not found>}"
echo "  COGNITO_USER_POOL_ID    = ${COGNITO_USER_POOL_ID:-<not found>}"
echo ""

# ── Install dependencies ──────────────────────────────────────────────────
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$_SCRIPT_DIR/01-lab1-multi-tenant-agents/requirements.txt" ]; then
  echo "Installing Python dependencies..."
  pip install -r "$_SCRIPT_DIR/01-lab1-multi-tenant-agents/requirements.txt" --quiet 2>/dev/null
  if [ -f "$_SCRIPT_DIR/02-lab2-multi-tenant-mcp-tool-gateway/requirements.txt" ]; then
    pip install -r "$_SCRIPT_DIR/02-lab2-multi-tenant-mcp-tool-gateway/requirements.txt" --quiet 2>/dev/null
  fi
  if [ -f "$_SCRIPT_DIR/05-lab5-per-tenant-usage-metering/requirements.txt" ]; then
    pip install -r "$_SCRIPT_DIR/05-lab5-per-tenant-usage-metering/requirements.txt" --quiet 2>/dev/null
  fi
  echo "  Done."
fi

# ── Install Docker (container runtime for ECS image builds) ───────────────
if ! command -v docker &> /dev/null; then
  echo "Installing Docker..."
  sudo dnf install -y docker --quiet 2>/dev/null
  sudo systemctl start docker
  sudo usermod -aG docker $(whoami) 2>/dev/null || true
  echo "  Docker installed ✓"
else
  # Ensure Docker is running
  sudo systemctl start docker 2>/dev/null || true
  echo "  Docker available ✓"
fi

# ── Install uv (required by starter toolkit for direct_code_deploy) ───────
if ! command -v uv &> /dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
  export PATH="$HOME/.local/bin:$PATH"
  echo "  uv installed ✓"
else
  echo "  uv available ✓"
fi

# ── Ensure Aurora app_user + properties table + RLS (for Lab 2 MCP Server) ─
_CLUSTER_ARN=$(_output AuroraClusterArn)
_DB_SECRET=$(_output DatabaseSecretArn)
_APP_USER_SECRET=$(_output AppUserSecretArn)

if [ -n "$_CLUSTER_ARN" ] && [ -n "$_DB_SECRET" ] && [ -n "$_APP_USER_SECRET" ]; then
  # Create app_user if it doesn't exist
  _APP_USER_EXISTS=$(aws rds-data execute-statement \
    --resource-arn "$_CLUSTER_ARN" --secret-arn "$_DB_SECRET" --database saasdb \
    --sql "SELECT 1 FROM pg_roles WHERE rolname='app_user'" \
    --query "records[0][0].longValue" --output text --no-cli-pager 2>/dev/null || echo "")

  if [ "$_APP_USER_EXISTS" != "1" ]; then
    echo "  Creating Aurora app_user..."
    aws rds-data execute-statement \
      --resource-arn "$_CLUSTER_ARN" --secret-arn "$_DB_SECRET" --database saasdb \
      --sql "CREATE ROLE app_user WITH LOGIN" \
      --no-cli-pager > /dev/null \
      || { echo "  ERROR: failed to create Aurora app_user" >&2; return 1 2>/dev/null || exit 1; }
    echo "  app_user created ✓"
  fi

  # Always sync app_user's password with AppUserSecret so Data API logins work.
  # PostgreSQL does not accept a bind parameter for PASSWORD, so the SQL is built
  # in Python with a dollar-quoted literal whose delimiter is guaranteed not to
  # appear in the password. Works for any password (quotes, $, backslashes, ...).
  _APP_USER_SQL=$(aws secretsmanager get-secret-value \
    --secret-id "$_APP_USER_SECRET" \
    --query "SecretString" --output text --no-cli-pager | python3 -c '
import sys, json
pw = json.load(sys.stdin)["password"]
tag = "pw"
while f"${tag}$" in pw or pw.endswith(f"${tag}"):
    tag += "_"
print(f"ALTER ROLE app_user WITH LOGIN PASSWORD ${tag}${pw}${tag}$")
') || { echo "  ERROR: could not read app_user secret ($_APP_USER_SECRET)" >&2; return 1 2>/dev/null || exit 1; }
  aws rds-data execute-statement \
    --resource-arn "$_CLUSTER_ARN" --secret-arn "$_DB_SECRET" --database saasdb \
    --sql "$_APP_USER_SQL" --no-cli-pager > /dev/null \
    || { echo "  ERROR: failed to set app_user password" >&2; return 1 2>/dev/null || exit 1; }
  unset _APP_USER_SQL
  echo "  app_user password synced ✓"

  # Create properties table + RLS if it doesn't exist
  _PROPS_EXISTS=$(aws rds-data execute-statement \
    --resource-arn "$_CLUSTER_ARN" --secret-arn "$_DB_SECRET" --database saasdb \
    --sql "SELECT 1 FROM information_schema.tables WHERE table_name='properties'" \
    --query "records[0][0].longValue" --output text --no-cli-pager 2>/dev/null || echo "")

  if [ "$_PROPS_EXISTS" != "1" ]; then
    echo "  Setting up properties table + RLS..."
    for _SQL in \
      "CREATE TABLE properties (property_id VARCHAR(50), tenant_id VARCHAR(50), address VARCHAR(200), status VARCHAR(50))" \
      "GRANT SELECT, INSERT, UPDATE, DELETE ON properties TO app_user" \
      "ALTER TABLE properties ENABLE ROW LEVEL SECURITY" \
      "CREATE POLICY tenant_isolation ON properties USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))" \
      "INSERT INTO properties VALUES ('P001','tenant-001','123 Main St','Active'),('P002','tenant-001','456 Oak Ave','Pending'),('P003','tenant-002','789 Pine Rd','Active'),('P004','tenant-002','321 Elm St','Sold')"; do
      aws rds-data execute-statement \
        --resource-arn "$_CLUSTER_ARN" --secret-arn "$_DB_SECRET" --database saasdb \
        --sql "$_SQL" --no-cli-pager > /dev/null \
        || { echo "  ERROR: statement failed: ${_SQL:0:60}..." >&2; return 1 2>/dev/null || exit 1; }
    done
    echo "  Properties table + RLS + sample data ✓"
  else
    echo "  Properties table exists ✓"
  fi
fi
