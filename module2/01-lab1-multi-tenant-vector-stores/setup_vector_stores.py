# SPDX-License-Identifier: MIT-0
"""
setup_vector_stores.py — Create vector stores and upload sample documents
Module 2, Lab 1

Creates:
  Aurora PostgreSQL (property_mgmt schema):
    - pgvector extension, property_mgmt schema, property_surveys table
    - HNSW index, RLS policy, app_user role

  Amazon OpenSearch Serverless:
    - maintenance_records index with knn_vector mapping and DLS roles per tenant

  Amazon S3 Vector Bucket:
    - Verifies the bucket exists (created by CloudFormation)
    - Creates per-tenant vector indexes for financial records

  Sample Documents:
    - Uploads local sample_docs/ to S3 in the format expected by ingest.py:
      s3://{BUCKET}/sample_docs/{tenant_id}/{doc_type}.txt

Usage:
    python setup_vector_stores.py
"""

import json
import os
import sys
from pathlib import Path

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

STACK_NAME = os.environ.get('STACK_NAME', 'workshop-infrastructure')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Set default region so any boto3.client() call works without explicit region_name
boto3.setup_default_session(region_name=AWS_REGION)


def _resolve_config():
    """Resolve config from env vars, falling back to CloudFormation stack outputs."""
    config = {
        'AURORA_CLUSTER_ARN': os.environ.get('AURORA_CLUSTER_ARN', ''),
        'AURORA_SECRET_ARN':  os.environ.get('AURORA_SECRET_ARN', ''),
        'APP_USER_SECRET_ARN': os.environ.get('APP_USER_SECRET_ARN', ''),
        'OPENSEARCH_ENDPOINT': os.environ.get('OPENSEARCH_ENDPOINT', ''),
        'S3_VECTOR_BUCKET':   os.environ.get('S3_VECTOR_BUCKET', ''),
    }
    if not all(config.values()):
        try:
            cfn = boto3.client('cloudformation', region_name=AWS_REGION)
            resp = cfn.describe_stacks(StackName=STACK_NAME)
            outputs = {o['OutputKey']: o['OutputValue'] for o in resp['Stacks'][0].get('Outputs', [])}
            if not config['AURORA_CLUSTER_ARN']:
                config['AURORA_CLUSTER_ARN'] = outputs.get('AuroraClusterArn', '')
            if not config['AURORA_SECRET_ARN']:
                config['AURORA_SECRET_ARN'] = outputs.get('DatabaseSecretArn', '')
            if not config['APP_USER_SECRET_ARN']:
                config['APP_USER_SECRET_ARN'] = outputs.get('AppUserSecretArn', '')
            if not config['OPENSEARCH_ENDPOINT']:
                config['OPENSEARCH_ENDPOINT'] = outputs.get('OpenSearchEndpoint', '')
            if not config['S3_VECTOR_BUCKET']:
                config['S3_VECTOR_BUCKET'] = outputs.get('DataBucketName', '')
        except Exception as e:
            print(f"Warning: Could not resolve CFN outputs: {e}")
    return config


AURORA_SQL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE SCHEMA IF NOT EXISTS property_mgmt",
    # Drop and recreate if dimension mismatch (idempotent for fresh installs)
    "DROP TABLE IF EXISTS property_mgmt.property_surveys CASCADE",
    """CREATE TABLE IF NOT EXISTS property_mgmt.property_surveys (
        id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        embedding vector(1024),
        chunks    TEXT,
        metadata  JSONB,
        tenantid  TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS property_surveys_hnsw ON property_mgmt.property_surveys
       USING hnsw (embedding vector_cosine_ops)""",
    "ALTER TABLE property_mgmt.property_surveys ENABLE ROW LEVEL SECURITY",
    """DO $body$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='property_surveys'
                       AND policyname='tenant_isolation') THEN
            CREATE POLICY tenant_isolation ON property_mgmt.property_surveys
                USING (tenantid = current_setting('app.current_tenant', true))
                WITH CHECK (tenantid = current_setting('app.current_tenant', true));
        END IF;
    END $body$""",
    """DO $body$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user') THEN
            CREATE ROLE app_user;
        END IF;
    END $body$""",
    "GRANT USAGE ON SCHEMA property_mgmt TO app_user",
    "GRANT SELECT, INSERT ON property_mgmt.property_surveys TO app_user",
]

OPENSEARCH_MAPPING = {
    "settings": {"index": {"knn": True, "knn.algo_param.ef_search": 512}},
    "mappings": {
        "properties": {
            "tenant_id": {"type": "keyword"},
            "text":      {"type": "text"},
            "embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "engine": "faiss",
                    "parameters": {"ef_construction": 512, "m": 16},
                    "space_type": "l2",
                },
            },
            "metadata": {"type": "text", "index": False},
        }
    },
}

# Local filename → (tenant_id, doc_type) mapping for S3 upload
FILE_MAP = {
    'tenant_001_property_survey.txt':     ('tenant-001', 'property_survey'),
    'tenant_001_maintenance_history.txt': ('tenant-001', 'maintenance_history'),
    'tenant_001_financial_records.txt':   ('tenant-001', 'financial_records'),
    'tenant_002_property_survey.txt':     ('tenant-002', 'property_survey'),
    'tenant_002_maintenance_history.txt': ('tenant-002', 'maintenance_history'),
    'tenant_002_financial_records.txt':   ('tenant-002', 'financial_records'),
}


# ---------------------------------------------------------------------------
# Aurora PostgreSQL Setup
# ---------------------------------------------------------------------------

def setup_aurora(cluster_arn, secret_arn, app_user_secret_arn, database='saasdb'):
    rds = boto3.client('rds-data', region_name=AWS_REGION)
    print("\n=== Aurora pgvector ===")

    # Fetch app_user password from Secrets Manager to set LOGIN credentials
    app_user_password = None
    if app_user_secret_arn:
        try:
            sm = boto3.client('secretsmanager', region_name=AWS_REGION)
            secret_value = sm.get_secret_value(SecretId=app_user_secret_arn)
            secret_json = json.loads(secret_value['SecretString'])
            app_user_password = secret_json.get('password', '')
        except Exception as e:
            print(f"  ERROR: Could not fetch app_user secret: {e}", file=sys.stderr)
            sys.exit(1)

    for sql in AURORA_SQL:
        preview = sql.strip().split('\n')[0][:60]
        print(f"  {preview}...")
        try:
            rds.execute_statement(resourceArn=cluster_arn, secretArn=secret_arn,
                                  database=database, sql=sql.strip())
        except Exception as e:
            print(f"  Warning: {e}")

    # Set app_user LOGIN and password (must match AppUserSecret for RDS Data API auth).
    # PostgreSQL's ALTER ROLE ... PASSWORD does not accept a bind parameter for the
    # password token, so the value is embedded as a dollar-quoted literal. A unique
    # tag that cannot occur in the password is used as the delimiter, so a password
    # containing single quotes (or a leading digit) can't terminate the literal early
    # or be injected — the previous single-quoted f-string broke on both.
    if app_user_password:
        print("  Setting app_user LOGIN + password...")
        # The secret's password may contain '$' (only "@/\ are excluded), so pick a
        # dollar-quote delimiter whose full $tag$ form does not appear in the password.
        tag = 'pw'
        while f"${tag}$" in app_user_password or app_user_password.endswith(f"${tag}"):
            tag += '_'
        try:
            rds.execute_statement(
                resourceArn=cluster_arn, secretArn=secret_arn, database=database,
                sql=f"ALTER ROLE app_user WITH LOGIN PASSWORD ${tag}${app_user_password}${tag}$",
            )
        except Exception as e:
            print(f"  ERROR: Could not set app_user password: {e}", file=sys.stderr)
            sys.exit(1)

    print("  done")


# ---------------------------------------------------------------------------
# OpenSearch Serverless Setup
# ---------------------------------------------------------------------------

def setup_opensearch(endpoint, tenant_ids):
    session = boto3.Session()
    creds = session.get_credentials()
    region = session.region_name or os.environ.get('AWS_REGION', 'us-east-1')
    auth = AWSV4SignerAuth(creds, region, 'aoss')
    host = endpoint.replace('https://', '').replace('http://', '').rstrip('/')
    client = OpenSearch(hosts=[{'host': host, 'port': 443}], http_auth=auth,
                        use_ssl=True, verify_certs=True,
                        connection_class=RequestsHttpConnection)
    print("\n=== OpenSearch ===")
    try:
        if client.indices.exists(index='maintenance_records'):
            client.indices.delete(index='maintenance_records')
            print("  maintenance_records index deleted (recreating with FAISS engine)")
        client.indices.create(index='maintenance_records', body=OPENSEARCH_MAPPING)
        print("  maintenance_records index created (FAISS engine)")
    except Exception as e:
        print(f"  Warning: {e}")
    # Note: DLS/FGAC for OpenSearch Serverless is managed via the
    # CloudFormation data access policy, not the _plugins/_security API.
    print("  done")


# ---------------------------------------------------------------------------
# S3 Vector Bucket Setup
# ---------------------------------------------------------------------------

def setup_s3_vectors(vector_bucket_name, tenant_ids):
    """Create per-tenant vector indexes in the S3 Vector Bucket."""
    print("\n=== S3 Vector Bucket ===")
    s3v = boto3.client('s3vectors', region_name=AWS_REGION)

    # Create the vector bucket if it doesn't exist
    try:
        s3v.get_vector_bucket(vectorBucketName=vector_bucket_name)
        print(f"  Vector bucket verified: {vector_bucket_name}")
    except s3v.exceptions.NotFoundException:
        print(f"  Creating vector bucket: {vector_bucket_name}")
        try:
            s3v.create_vector_bucket(vectorBucketName=vector_bucket_name)
            print(f"  Vector bucket created: {vector_bucket_name}")
        except Exception as e:
            print(f"  ERROR: Could not create vector bucket: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"  ERROR: vector bucket {vector_bucket_name!r} not accessible: {e}")
        sys.exit(1)

    # Create a per-tenant index for financial records
    for tenant_id in tenant_ids:
        index_name = f"{tenant_id}-financial-records"
        try:
            s3v.get_index(vectorBucketName=vector_bucket_name, indexName=index_name)
            print(f"  Index already exists: {index_name}")
        except s3v.exceptions.NotFoundException:
            s3v.create_index(
                vectorBucketName=vector_bucket_name,
                indexName=index_name,
                dimension=1024,
                distanceMetric='cosine',
                dataType='float32',
                metadataConfiguration={
                    'nonFilterableMetadataKeys': ['source_text'],
                },
            )
            print(f"  Created index: {index_name}")
        except Exception as e:
            print(f"  Warning ({index_name}): {e}")

    print("  done")


# ---------------------------------------------------------------------------
# Upload Sample Documents to S3
# ---------------------------------------------------------------------------

def upload_sample_docs(bucket_name):
    """Upload local sample documents to S3 for the ingestion Lambda."""
    print("\n=== Upload Sample Documents in S3 Bucket ===")
    s3 = boto3.client('s3', region_name=AWS_REGION)
    sample_dir = Path(__file__).parent / 'sample_docs'

    if not sample_dir.exists():
        print(f"  WARNING: {sample_dir} not found — skipping upload")
        return

    print(f"  Target: s3://{bucket_name}/sample_docs/")

    for filename, (tenant_id, doc_type) in FILE_MAP.items():
        local_path = sample_dir / filename
        if not local_path.exists():
            print(f"  SKIP: {filename} (not found)")
            continue

        s3_key = f"sample_docs/{tenant_id}/{doc_type}.txt"
        s3.upload_file(str(local_path), bucket_name, s3_key)
        print(f"  ✓ {s3_key}")

    print("  done")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = _resolve_config()
    cluster_arn        = cfg['AURORA_CLUSTER_ARN']
    secret_arn         = cfg['AURORA_SECRET_ARN']
    app_user_secret    = cfg['APP_USER_SECRET_ARN']
    os_endpoint        = cfg['OPENSEARCH_ENDPOINT']
    s3_bucket          = cfg['S3_VECTOR_BUCKET']
    tenant_ids  = [t.strip() for t in os.environ.get('TENANT_IDS', 'tenant-001,tenant-002').split(',') if t.strip()]
    database    = os.environ.get('AURORA_DB_NAME', 'saasdb')

    missing = []
    if not cluster_arn: missing.append('AURORA_CLUSTER_ARN')
    if not secret_arn:  missing.append('AURORA_SECRET_ARN')
    if not os_endpoint: missing.append('OPENSEARCH_ENDPOINT')
    if not s3_bucket:   missing.append('S3_VECTOR_BUCKET')
    if missing:
        print(f"ERROR: Could not resolve: {', '.join(missing)}")
        print(f"Ensure the '{STACK_NAME}' stack is deployed, or set env vars manually.")
        sys.exit(1)

    upload_sample_docs(s3_bucket)
    setup_aurora(cluster_arn, secret_arn, app_user_secret, database)
    setup_opensearch(os_endpoint, tenant_ids)
    setup_s3_vectors(s3_bucket, tenant_ids)

    print("\nAll stores ready. Sample documents uploaded.")


if __name__ == '__main__':
    main()
