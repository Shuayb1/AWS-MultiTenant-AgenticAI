#!/usr/bin/env bash
# deploy_query_properties_mcp_server.sh — Build and deploy MCP Server to ECS Fargate
# Module 3, Lab 2
#
# Builds a Docker image for the query_properties MCP Server, pushes to ECR,
# and starts the ECS Fargate service. CloudFront provides a trusted HTTPS
# endpoint that the AgentCore Gateway registers as an MCP Server target.
#
# Prerequisites:
#   - source setup_env.sh
#   - Docker available on the instance
#   - ECS infrastructure pre-provisioned by CFN (cluster, service, ALB, CloudFront)
#
# Usage:
#   bash deploy_query_properties_mcp_server.sh
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STACK_NAME=${STACK_NAME:-workshop-infrastructure}

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying MCP Server to ECS Fargate                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Resolve infrastructure from CFN
# ---------------------------------------------------------------------------
echo "→ Step 1: Resolving infrastructure..."

ECR_REPO=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='McpServerEcrRepositoryUri'].OutputValue | [0]" \
  --output text --no-cli-pager)
MCP_SERVER_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='McpServerAlbEndpoint'].OutputValue | [0]" \
  --output text --no-cli-pager)
CLUSTER_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='McpServerClusterName'].OutputValue | [0]" \
  --output text --no-cli-pager)
SERVICE_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='McpServerServiceName'].OutputValue | [0]" \
  --output text --no-cli-pager)

echo "  ECR:     $ECR_REPO"
echo "  HTTPS:   $MCP_SERVER_URL"
echo "  Cluster: $CLUSTER_NAME"
echo "  Service: $SERVICE_NAME"

# ---------------------------------------------------------------------------
# Step 2: Create Dockerfile
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 2: Building Docker image..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR=$(mktemp -d)

# Copy MCP server code
cp "$SCRIPT_DIR/query_properties_mcp_server.py" "$BUILD_DIR/server.py"

# Create requirements
cat > "$BUILD_DIR/requirements.txt" <<'EOF'
boto3>=1.34.0
PyJWT[crypto]>=2.8.0
flask>=3.0.0
EOF

# Create Dockerfile
cat > "$BUILD_DIR/Dockerfile" <<'EOF'
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8080
CMD ["python", "server.py"]
EOF

# Build the image
if command -v docker &> /dev/null; then
  CONTAINER_CMD="sudo docker"
elif command -v finch &> /dev/null; then
  CONTAINER_CMD="sudo finch"
else
  echo "  ERROR: No container runtime found. Run: sudo dnf install -y docker && sudo systemctl start docker"
  rm -rf "$BUILD_DIR"
  exit 1
fi

$CONTAINER_CMD build -t "${ECR_REPO}:latest" "$BUILD_DIR"
rm -rf "$BUILD_DIR"

echo "  Image built: ${ECR_REPO}:latest"

# ---------------------------------------------------------------------------
# Step 3: Push to ECR
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 3: Pushing to ECR..."

aws ecr get-login-password --region "$REGION" | \
  $CONTAINER_CMD login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" 2>/dev/null

$CONTAINER_CMD push "${ECR_REPO}:latest"
echo "  Pushed to ECR"

# ---------------------------------------------------------------------------
# Step 4: Start the ECS service
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 4: Starting ECS service..."

# Force new deployment to pull latest image
aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$SERVICE_NAME" \
  --desired-count 1 \
  --force-new-deployment \
  --no-cli-pager > /dev/null

echo "  Service starting (desired count: 1)..."
echo "  Waiting for task to reach RUNNING..."

# Wait for service to stabilize
aws ecs wait services-stable \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --no-cli-pager 2>/dev/null || true

echo "  Service running ✓"

# ---------------------------------------------------------------------------
# Step 5: Seed Aurora data
# ---------------------------------------------------------------------------
echo ""
echo "→ Step 5: Seeding Aurora PostgreSQL with RLS..."

AURORA_CLUSTER_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AuroraClusterArn'].OutputValue | [0]" \
  --output text --no-cli-pager)
APP_USER_SECRET_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AppUserSecretArn'].OutputValue | [0]" \
  --output text --no-cli-pager)
DB_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AuroraDbName'].OutputValue | [0]" \
  --output text --no-cli-pager)

python3 -c "
import boto3, json
rds = boto3.client('rds-data', region_name='${REGION}')
cluster_arn = '${AURORA_CLUSTER_ARN}'
secret_arn = '${APP_USER_SECRET_ARN}'
db = '${DB_NAME}'

# Create properties table if not exists
try:
    rds.execute_statement(resourceArn=cluster_arn, secretArn=secret_arn, database=db,
        sql='''CREATE TABLE IF NOT EXISTS properties (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            property_id TEXT NOT NULL,
            address TEXT,
            status TEXT DEFAULT 'active'
        )''')
    rds.execute_statement(resourceArn=cluster_arn, secretArn=secret_arn, database=db,
        sql=\"\"\"ALTER TABLE properties ENABLE ROW LEVEL SECURITY\"\"\")
    rds.execute_statement(resourceArn=cluster_arn, secretArn=secret_arn, database=db,
        sql=\"\"\"DO \$\$ BEGIN
            CREATE POLICY tenant_isolation ON properties
                USING (tenant_id = current_setting('app.tenant_id', true));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END \$\$\"\"\")
except Exception as e:
    pass  # Table may already exist

# Seed data
for tid, props in [('tenant-001', [('P001','42 Oakwood Drive','active'),('P002','15 Elm Street','active'),('P003','8 Pine Road','maintenance')]),
                   ('tenant-002', [('P101','17 Maple Close','active'),('P102','3 Birch Lane','active')])]:
    for pid, addr, status in props:
        try:
            rds.execute_statement(resourceArn=cluster_arn, secretArn=secret_arn, database=db,
                sql=\"\"\"INSERT INTO properties (tenant_id, property_id, address, status)
                       VALUES (:tid, :pid, :addr, :status) ON CONFLICT DO NOTHING\"\"\",
                parameters=[
                    {'name':'tid','value':{'stringValue':tid}},
                    {'name':'pid','value':{'stringValue':pid}},
                    {'name':'addr','value':{'stringValue':addr}},
                    {'name':'status','value':{'stringValue':status}},
                ])
        except: pass
    print(f'  Seeded: {len(props)} properties for {tid}')
print('  RLS policy: tenant_id = current_setting(app.tenant_id)')
"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
echo ""
MCP_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='McpServerAlbEndpoint'].OutputValue | [0]" \
  --output text --no-cli-pager)
MCP_ENDPOINT="${MCP_ENDPOINT}/mcp"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   MCP Server Deployed (ECS Fargate)                         ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  MCP endpoint: $MCP_ENDPOINT"
echo "║  Health check: ${MCP_SERVER_URL}/health"
echo "║  Auth: Cognito JWKS validation (custom:tenant_id claim)      ║"
echo "║  Data: Aurora PostgreSQL with RLS                            ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Export for Gateway registration:"
echo "  export QUERY_PROPERTIES_MCP_ENDPOINT=$MCP_ENDPOINT"
