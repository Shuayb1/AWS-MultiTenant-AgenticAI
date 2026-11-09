#!/bin/bash
# setup_rls.sh — Create the pooled schema, app_user role, RLS policy, and stored function
# Module 1, Lab 4
#
# Usage: source /workshop/module1/setup_env.sh && bash setup_rls.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../setup_env.sh" 2>/dev/null || true

: "${AURORA_CLUSTER_ARN:?AURORA_CLUSTER_ARN is not set}"
: "${AURORA_SECRET_ARN:?AURORA_SECRET_ARN is not set}"
DATABASE="${AURORA_DB_NAME:-saasdb}"

echo "=== Aurora RLS Setup ==="

# 1. Create the documents table (pooled model — all tenants share one table)
echo "  Creating documents table... "
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "$DATABASE" \
  --sql "CREATE TABLE IF NOT EXISTS documents (
    id         VARCHAR(255) NOT NULL,
    tenant_id  VARCHAR(255) NOT NULL,
    title      TEXT,
    content    TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (id, tenant_id)
  )" --output text --no-cli-pager > /dev/null
echo "done"

# 2. Create app_user role (must NOT be table owner — owners bypass RLS)
echo "  Creating app_user role... "
APP_USER_SECRET_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-workshop-infrastructure}" \
  --query "Stacks[0].Outputs[?OutputKey=='AppUserSecretArn'].OutputValue" \
  --output text --no-cli-pager 2>/dev/null || echo "")

if [ -z "$APP_USER_SECRET_ARN" ] || [ "$APP_USER_SECRET_ARN" = "None" ]; then
  # Fallback to hardcoded name
  APP_USER_SECRET_ARN="workshop-infrastructure-app-user-secret"
fi

# PostgreSQL does not accept a bind parameter for PASSWORD, so build the SQL in
# Python using a dollar-quoted literal whose delimiter is guaranteed not to appear
# in the password. Works for any password (quotes, $, backslashes, ...).
APP_USER_SQL=$(aws secretsmanager get-secret-value \
  --secret-id "$APP_USER_SECRET_ARN" \
  --query 'SecretString' --output text --no-cli-pager | python3 -c '
import sys, json
pw = json.load(sys.stdin)["password"]
tag = "pw"
while f"${tag}$" in pw or pw.endswith(f"${tag}"):
    tag += "_"
print(f"ALTER ROLE app_user WITH LOGIN PASSWORD ${tag}${pw}${tag}$")
') || { echo "FAILED: could not read app_user secret ($APP_USER_SECRET_ARN)" >&2; exit 1; }

APP_USER_EXISTS=$(aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "$DATABASE" \
  --sql "SELECT 1 FROM pg_roles WHERE rolname = 'app_user'" \
  --query "records[0][0].longValue" --output text --no-cli-pager) \
  || { echo "FAILED: could not check for app_user role" >&2; exit 1; }

if [ "$APP_USER_EXISTS" != "1" ]; then
  aws rds-data execute-statement \
    --resource-arn "$AURORA_CLUSTER_ARN" \
    --secret-arn "$AURORA_SECRET_ARN" \
    --database "$DATABASE" \
    --sql "CREATE ROLE app_user WITH LOGIN" \
    --output text --no-cli-pager > /dev/null \
    || { echo "FAILED: could not create app_user role" >&2; exit 1; }
fi

# Always sync the password with AppUserSecret (safe to re-run)
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "$DATABASE" \
  --sql "$APP_USER_SQL" --output text --no-cli-pager > /dev/null \
  || { echo "FAILED: could not set app_user password" >&2; exit 1; }
unset APP_USER_SQL
echo "done"

# 3. Grant permissions to app_user
echo "  Granting permissions... "
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "$DATABASE" \
  --sql "GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_user" \
  --output text --no-cli-pager > /dev/null
echo "done"

# 4. Enable RLS on the documents table
echo "  Enabling RLS... "
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "$DATABASE" \
  --sql "ALTER TABLE documents ENABLE ROW LEVEL SECURITY" \
  --output text --no-cli-pager > /dev/null
echo "done"

# 5. Create the tenant isolation policy
#    USING: filters SELECT/UPDATE/DELETE to rows where tenant_id matches the session var
#    WITH CHECK: prevents INSERT/UPDATE of rows with a different tenant_id
echo "  Creating tenant_isolation_policy... "
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "$DATABASE" \
  --sql "DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_policy') THEN
      CREATE POLICY tenant_isolation_policy ON documents
        USING (tenant_id = current_setting('app.current_tenant'))
        WITH CHECK (tenant_id = current_setting('app.current_tenant'));
    END IF;
  END
  \$\$" --output text --no-cli-pager > /dev/null
echo "done"

# 6. Create the stored function (alternative to explicit transactions)
#    Wraps SET + query atomically in a single call
echo "  Creating execute_as_tenant function... "
aws rds-data execute-statement \
  --resource-arn "$AURORA_CLUSTER_ARN" \
  --secret-arn "$AURORA_SECRET_ARN" \
  --database "$DATABASE" \
  --sql "CREATE OR REPLACE FUNCTION execute_as_tenant(p_tenant_id TEXT, p_sql TEXT)
  RETURNS SETOF RECORD AS \$\$
  BEGIN
    PERFORM set_config('app.current_tenant', p_tenant_id, false);
    RETURN QUERY EXECUTE p_sql;
  END;
  \$\$ LANGUAGE plpgsql SECURITY DEFINER" --output text --no-cli-pager > /dev/null
echo "done"

echo ""
echo "Schema and RLS ready."
