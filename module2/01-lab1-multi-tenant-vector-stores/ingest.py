# SPDX-License-Identifier: MIT-0
"""
ingest.py — Lambda function to ingest sample documents into all three vector stores
Module 2, Lab 1

Reads sample property management documents from S3, chunks them, embeds via
Amazon Nova Multimodal Embeddings, and stores in Aurora pgvector, OpenSearch
(via Bedrock KB), and S3 Vectors.

Tasks:
    1.1 — chunk_text: split document text into overlapping fixed-size chunks
    1.2 — embed:      call Bedrock Nova Embeddings, return 1024-dim vector
    1.3 — Deploy:     deploy this file as a Lambda function (deploy.sh)
    1.4 — Invoke:     invoke the Lambda to start ingestion

Invocation (Lambda event):
    {
        "tenant_ids": ["tenant-001", "tenant-002"],   // optional, defaults to all
        "doc_types": ["property_survey", ...]         // optional, defaults to all
    }

    Or invoke locally:
        python ingest.py

Environment Variables:
    AURORA_CLUSTER_ARN   — Aurora Serverless cluster ARN
    APP_USER_SECRET_ARN  — Secrets Manager ARN for the app DB user
    AURORA_DB_NAME       — Database name (default: saasdb)
    S3_VECTOR_BUCKET     — S3 bucket for vector storage and KB data source
    SOURCE_BUCKET        — S3 bucket containing sample documents
    SOURCE_PREFIX        — S3 key prefix for sample docs (default: sample_docs/)
    AWS_REGION           — Deployment region (default: us-east-1)
    STACK_NAME           — CloudFormation stack name for config resolution
"""

import json
import os
import sys
from typing import List, Dict

import boto3


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STACK_NAME = os.environ.get('STACK_NAME', 'workshop-infrastructure')
AWS_REGION = os.environ.get('AWS_REGION_NAME', os.environ.get('AWS_REGION', 'us-east-1'))

boto3.setup_default_session(region_name=AWS_REGION)


def _resolve_config():
    """Resolve config from env vars, falling back to CloudFormation stack outputs."""
    config = {
        'AURORA_CLUSTER_ARN':  os.environ.get('AURORA_CLUSTER_ARN', ''),
        'APP_USER_SECRET_ARN': os.environ.get('APP_USER_SECRET_ARN', ''),
        'S3_VECTOR_BUCKET':    os.environ.get('S3_VECTOR_BUCKET', ''),
        'SOURCE_BUCKET':       os.environ.get('SOURCE_BUCKET', ''),
    }
    if not all([config['AURORA_CLUSTER_ARN'], config['APP_USER_SECRET_ARN'], config['S3_VECTOR_BUCKET']]):
        try:
            cfn = boto3.client('cloudformation', region_name=AWS_REGION)
            resp = cfn.describe_stacks(StackName=STACK_NAME)
            outputs = {o['OutputKey']: o['OutputValue'] for o in resp['Stacks'][0].get('Outputs', [])}
            if not config['AURORA_CLUSTER_ARN']:
                config['AURORA_CLUSTER_ARN'] = outputs.get('AuroraClusterArn', '')
            if not config['APP_USER_SECRET_ARN']:
                config['APP_USER_SECRET_ARN'] = outputs.get('AppUserSecretArn', outputs.get('DatabaseSecretArn', ''))
            if not config['S3_VECTOR_BUCKET']:
                config['S3_VECTOR_BUCKET'] = outputs.get('DataBucketName', '')
            if not config['SOURCE_BUCKET']:
                config['SOURCE_BUCKET'] = outputs.get('DataBucketName', '')
        except Exception as e:
            print(f"Warning: Could not resolve CFN outputs: {e}")
    return config


_CFG = _resolve_config()
AURORA_CLUSTER_ARN  = _CFG['AURORA_CLUSTER_ARN']
APP_USER_SECRET_ARN = _CFG['APP_USER_SECRET_ARN']
DATABASE            = os.environ.get('AURORA_DB_NAME', 'saasdb')
S3_VECTOR_BUCKET    = _CFG['S3_VECTOR_BUCKET']
SOURCE_BUCKET       = _CFG['SOURCE_BUCKET'] or S3_VECTOR_BUCKET
SOURCE_PREFIX       = os.environ.get('SOURCE_PREFIX', 'sample_docs/')

EMBED_MODEL   = 'amazon.nova-2-multimodal-embeddings-v1:0'
EMBED_DIM     = 1024
CHUNK_SIZE    = 400
CHUNK_OVERLAP = 50

# Tenant → document type → S3 key mapping
# Documents are expected at: s3://{SOURCE_BUCKET}/{SOURCE_PREFIX}{tenant_id}/{doc_type}.txt
TENANT_IDS = ['tenant-001', 'tenant-002']
DOC_TYPES  = ['property_survey', 'maintenance_history', 'financial_records']

# Each document type is stored in a specific vector store:
#   property_survey     → Aurora pgvector (Lab 2: function calling tool)
#   maintenance_history → S3 for Bedrock KB data source (Lab 2: Bedrock KB tool)
#   financial_records   → S3 Vectors (Lab 2: MCP server tool)
DOC_TYPE_TO_STORE = {
    'property_survey': 'aurora',
    'maintenance_history': 'kb_datasource',
    'financial_records': 's3',
}


# ---------------------------------------------------------------------------
# S3 Document Reader
# ---------------------------------------------------------------------------

def read_document_from_s3(s3_client, tenant_id: str, doc_type: str) -> str:
    """
    Read a sample document from S3.

    Expected key format: {SOURCE_PREFIX}{tenant_id}/{doc_type}.txt
    Example: sample_docs/tenant-001/property_survey.txt

    Args:
        s3_client: boto3 S3 client
        tenant_id: tenant identifier (e.g. 'tenant-001')
        doc_type:  document type (e.g. 'property_survey')

    Returns:
        Document text content, or empty string if not found.
    """
    key = f"{SOURCE_PREFIX}{tenant_id}/{doc_type}.txt"
    try:
        resp = s3_client.get_object(Bucket=SOURCE_BUCKET, Key=key)
        return resp['Body'].read().decode('utf-8')
    except s3_client.exceptions.NoSuchKey:
        print(f"  WARNING: s3://{SOURCE_BUCKET}/{key} not found — skipping")
        return ''
    except Exception as e:
        print(f"  WARNING: Error reading s3://{SOURCE_BUCKET}/{key}: {e}")
        return ''


# ---------------------------------------------------------------------------
# Task 1.1 — Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Split text into overlapping fixed-size chunks.

    Walk through the text in steps of (chunk_size - overlap), extracting
    a slice of chunk_size characters at each step. Strip whitespace and
    skip empty chunks.

    Args:
        text:       The full document text to split.
        chunk_size: Maximum characters per chunk (default: CHUNK_SIZE).
        overlap:    Characters of overlap between consecutive chunks (default: CHUNK_OVERLAP).

    Returns:
        List of non-empty chunk strings.

    Example:
        text = "AAAA BBBB CCCC DDDD EEEE"  (chunk_size=15, overlap=5)
        -> ["AAAA BBBB CCCC", "CCCC DDDD EEEE", ...]

    Raises:
        NotImplementedError: until Task 1.1 is complete.
    """
    # TODO: Task 1.1 — implement fixed-size chunking with overlap
    raise NotImplementedError("Complete Task 1.1 — chunk_text")  # REPLACE THIS LINE


# ---------------------------------------------------------------------------
# Task 1.2 — Embedding
# ---------------------------------------------------------------------------

def embed(text: str, bedrock) -> List[float]:
    """
    Generate a 1024-dimensional embedding for text via Amazon Nova Multimodal Embeddings.

    Calls bedrock-runtime invoke_model with modelId=EMBED_MODEL.
    The request body uses the Nova Embeddings format:
        {"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": {...}}
    The response body contains {"embeddings": [{"embedding": [float, ...]}]}.

    Args:
        text:    Input text to embed. Should be a single chunk, not a full document.
        bedrock: boto3 bedrock-runtime client.

    Returns:
        List of 1024 floats.

    Raises:
        NotImplementedError: until Task 1.2 is complete.
    """
    # TODO: Task 1.2 — call bedrock.invoke_model with EMBED_MODEL, return embedding list
    raise NotImplementedError("Complete Task 1.2 — embed")  # REPLACE THIS LINE


# ---------------------------------------------------------------------------
# Helpers (already implemented)
# ---------------------------------------------------------------------------

def set_tenant_session_aurora(rds_data, tenant_id: str) -> str:
    """
    Activate the RLS policy for the current Aurora connection.

    Sets the PostgreSQL session variable that the RLS policy evaluates:
        USING (tenantid = current_setting('app.current_tenant')::BIGINT)

    Must be called before every INSERT or SELECT on property_mgmt.property_surveys.
    Returns a transactionId for subsequent statements.
    """
    txn = rds_data.begin_transaction(
        resourceArn=AURORA_CLUSTER_ARN,
        secretArn=APP_USER_SECRET_ARN,
        database=DATABASE,
    )
    transaction_id = txn['transactionId']
    rds_data.execute_statement(
        resourceArn=AURORA_CLUSTER_ARN,
        secretArn=APP_USER_SECRET_ARN,
        database=DATABASE,
        transactionId=transaction_id,
        sql="SELECT set_config('app.current_tenant', :tid, false)",
        parameters=[{'name': 'tid', 'value': {'stringValue': tenant_id}}],
    )
    return transaction_id


# ---------------------------------------------------------------------------
# Ingest into Aurora pgvector (pre-implemented)
# ---------------------------------------------------------------------------

def ingest_aurora(rds_data, tenant_id: str, chunks: List[Dict]) -> int:
    """
    Store chunks in Aurora property_mgmt.property_surveys with RLS session active.

    Calls set_tenant_session_aurora() first to activate the RLS policy,
    then INSERTs each chunk. The RLS policy ensures rows are only visible
    to the session whose tenantid matches.

    Args:
        rds_data:  boto3 rds-data client
        tenant_id: validated tenant identifier, e.g. 'tenant-001'
        chunks:    list of {text, embedding, metadata} dicts

    Returns:
        number of chunks stored
    """
    transaction_id = set_tenant_session_aurora(rds_data, tenant_id)
    for chunk in chunks:
        rds_data.execute_statement(
            resourceArn=AURORA_CLUSTER_ARN,
            secretArn=APP_USER_SECRET_ARN,
            database=DATABASE,
            transactionId=transaction_id,
            sql=(
                "INSERT INTO property_mgmt.property_surveys (embedding, chunks, metadata, tenantid) "
                "VALUES (:embedding::vector, :chunks, :metadata::jsonb, :tenantid)"
            ),
            parameters=[
                {'name': 'embedding', 'value': {'stringValue': str(chunk['embedding'])}},
                {'name': 'chunks',    'value': {'stringValue': chunk['text']}},
                {'name': 'metadata',  'value': {'stringValue': json.dumps(chunk['metadata'])}},
                {'name': 'tenantid',  'value': {'stringValue': tenant_id}},
            ],
        )
    rds_data.commit_transaction(
        resourceArn=AURORA_CLUSTER_ARN,
        secretArn=APP_USER_SECRET_ARN,
        transactionId=transaction_id,
    )
    return len(chunks)


# ---------------------------------------------------------------------------
# Upload to Bedrock KB data source (pre-implemented)
# ---------------------------------------------------------------------------

def ingest_kb_datasource(s3_client, tenant_id: str, doc_type: str, text: str) -> int:
    """
    Upload the raw document to S3 for Bedrock Knowledge Base ingestion.

    The KB handles chunking, embedding, and indexing into OpenSearch
    automatically. We upload the raw text file with tenant_id metadata
    so the KB can filter by tenant at retrieval time.

    Args:
        s3_client: boto3 s3 client
        tenant_id: validated tenant identifier
        doc_type:  document type name (used in the S3 key)
        text:      raw document text content

    Returns:
        1 (one document uploaded)
    """
    key = f"kb-datasource/{tenant_id}/{doc_type}.txt"
    s3_client.put_object(
        Bucket=S3_VECTOR_BUCKET,
        Key=key,
        Body=text.encode('utf-8'),
        ContentType='text/plain',
        Metadata={'tenant_id': tenant_id},
    )
    return 1


# ---------------------------------------------------------------------------
# Ingest into S3 Vector Bucket (pre-implemented)
# ---------------------------------------------------------------------------

def ingest_s3_vectors(s3v_client, tenant_id: str, chunks: List[Dict]) -> int:
    """
    Store chunks in the tenant's S3 Vectors index using the PutVectors API.

    Each tenant has a dedicated vector index: {tenant_id}-financial-records.
    The index was created by setup_vector_stores.py. Isolation is enforced
    by the index-per-tenant model — the agent only queries its own index.

    Args:
        s3v_client: boto3 s3vectors client
        tenant_id:  validated tenant identifier
        chunks:     list of {text, embedding, metadata} dicts

    Returns:
        number of vectors stored
    """
    index_name = f"{tenant_id}-financial-records"
    vectors = []
    for i, chunk in enumerate(chunks):
        doc_id = chunk['metadata'].get('document_id', 'doc')
        vectors.append({
            'key': f"{doc_id}-chunk-{i}",
            'data': {'float32': chunk['embedding']},
            'metadata': {
                'source_text': chunk['text'],
                'tenant_id': tenant_id,
                'doc_type': chunk['metadata'].get('doc_type', 'financial_records'),
            },
        })
    s3v_client.put_vectors(
        vectorBucketName=S3_VECTOR_BUCKET,
        indexName=index_name,
        vectors=vectors,
    )
    return len(vectors)


# ---------------------------------------------------------------------------
# Core ingestion logic
# ---------------------------------------------------------------------------

def ingest(tenant_ids: List[str], doc_types: List[str]) -> Dict:
    """
    Run the ingestion pipeline for the specified tenants and document types.

    Args:
        tenant_ids: list of tenant identifiers to process
        doc_types:  list of document types to ingest

    Returns:
        Summary dict with counts per tenant/doc_type.
    """
    bedrock    = boto3.client('bedrock-runtime', region_name=AWS_REGION)
    rds_data   = boto3.client('rds-data', region_name=AWS_REGION)
    s3_client  = boto3.client('s3', region_name=AWS_REGION)
    s3v_client = boto3.client('s3vectors', region_name=AWS_REGION)

    results = {}

    for tenant_id in tenant_ids:
        print(f"\n=== Ingesting for {tenant_id} ===")
        results[tenant_id] = {}

        for doc_type in doc_types:
            target_store = DOC_TYPE_TO_STORE.get(doc_type)
            if not target_store:
                print(f"  WARNING: Unknown doc_type '{doc_type}' — skipping")
                continue

            print(f"  {doc_type} → {target_store}")

            # Read document from S3
            text = read_document_from_s3(s3_client, tenant_id, doc_type)
            if not text:
                results[tenant_id][doc_type] = {'status': 'skipped', 'reason': 'not found'}
                continue

            # KB data source: upload raw file (KB handles chunking/embedding)
            if target_store == 'kb_datasource':
                try:
                    n = ingest_kb_datasource(s3_client, tenant_id, doc_type, text)
                    print(f"    Uploaded: {n} document (KB will chunk and embed)")
                    results[tenant_id][doc_type] = {'status': 'ok', 'documents': n}
                except NotImplementedError as e:
                    print(f"    {e}")
                    results[tenant_id][doc_type] = {'status': 'not_implemented'}
                continue

            # Other stores: chunk and embed locally
            try:
                chunk_texts = chunk_text(text)
            except NotImplementedError:
                print("    Task 1.1 not yet implemented — complete chunk_text()")
                results[tenant_id][doc_type] = {'status': 'not_implemented'}
                return results

            print(f"    {len(chunk_texts)} chunks")

            # Embed each chunk
            chunks = []
            for i, ct in enumerate(chunk_texts):
                try:
                    embedding = embed(ct, bedrock)
                except NotImplementedError:
                    print("    Task 1.2 not yet implemented — complete embed()")
                    results[tenant_id][doc_type] = {'status': 'not_implemented'}
                    return results
                chunks.append({
                    'text':      ct,
                    'embedding': embedding,
                    'metadata': {
                        'document_id': f"{tenant_id}_{doc_type}",
                        'tenant_id':   tenant_id,
                        'doc_type':    doc_type,
                        'chunk_index': i,
                        'source':      f"{doc_type}.txt",
                    },
                })

            # Route to the correct store
            try:
                if target_store == 'aurora':
                    n = ingest_aurora(rds_data, tenant_id, chunks)
                elif target_store == 's3':
                    n = ingest_s3_vectors(s3v_client, tenant_id, chunks)
                else:
                    n = 0
                print(f"    Stored: {n} chunks")
                results[tenant_id][doc_type] = {'status': 'ok', 'chunks': n}
            except NotImplementedError as e:
                print(f"    {e}")
                results[tenant_id][doc_type] = {'status': 'not_implemented'}

    print("\nIngestion complete.")
    return results


# ---------------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    AWS Lambda entry point for the ingestion pipeline.

    Event format:
        {
            "tenant_ids": ["tenant-001", "tenant-002"],  // optional
            "doc_types": ["property_survey", ...]        // optional
        }

    Returns:
        {
            "statusCode": 200,
            "body": { "tenant-001": { "property_survey": {"status": "ok", "chunks": 4}, ... } }
        }
    """
    tenant_ids = event.get('tenant_ids', TENANT_IDS)
    doc_types = event.get('doc_types', DOC_TYPES)

    print(f"Ingestion triggered: tenants={tenant_ids}, doc_types={doc_types}")
    print(f"Source: s3://{SOURCE_BUCKET}/{SOURCE_PREFIX}")

    results = ingest(tenant_ids, doc_types)

    return {
        'statusCode': 200,
        'body': json.dumps(results, default=str),
    }


# ---------------------------------------------------------------------------
# Local execution
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # When run locally, simulate a Lambda invocation with all tenants/doc_types
    event = {
        'tenant_ids': TENANT_IDS,
        'doc_types': DOC_TYPES,
    }
    result = lambda_handler(event, None)
    print(f"\nResult: {json.dumps(json.loads(result['body']), indent=2)}")
