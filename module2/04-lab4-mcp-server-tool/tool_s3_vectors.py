# SPDX-License-Identifier: MIT-0
"""
tool_s3_vectors.py — Retrieval Tool via MCP Server (Amazon S3 Vectors)
Module 2, Lab 4

Implements a tenant-isolated retrieval tool as an MCP (Model Context Protocol)
server. The agent connects via stdio and calls the search_financial_docs tool.
Tenant isolation is enforced by S3 key prefix scoping.

Pattern: MCP Server
  - Tool runs as a separate process
  - Agent communicates via the MCP protocol (stdio)
  - Tool server enforces tenant isolation independently
  - Any MCP-compatible agent can use this tool

Usage (as MCP server):
    python tool_s3_vectors.py

Usage (standalone test):
    python tool_s3_vectors.py --test
"""

import json
import os
import sys
from typing import Dict, List

import boto3
from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------------------
# Configuration (auto-resolved from CloudFormation)
# ---------------------------------------------------------------------------

STACK_NAME = os.environ.get('STACK_NAME', 'workshop-infrastructure')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
EMBED_MODEL = 'amazon.nova-2-multimodal-embeddings-v1:0'
EMBED_DIM = 1024

# Set default region so any boto3.client() call works without explicit region_name
boto3.setup_default_session(region_name=AWS_REGION)


def _resolve_config():
    config = {'S3_VECTOR_BUCKET': os.environ.get('S3_VECTOR_BUCKET', '')}
    if not config['S3_VECTOR_BUCKET']:
        try:
            cfn = boto3.client('cloudformation', region_name=AWS_REGION)
            resp = cfn.describe_stacks(StackName=STACK_NAME)
            outputs = {o['OutputKey']: o['OutputValue'] for o in resp['Stacks'][0].get('Outputs', [])}
            config['S3_VECTOR_BUCKET'] = outputs.get('DataBucketName', '')
        except Exception as e:
            print(f"Warning: Could not resolve CFN outputs: {e}", file=sys.stderr)
    return config


_CFG = _resolve_config()
S3_VECTOR_BUCKET = _CFG['S3_VECTOR_BUCKET']

# Create the MCP server
# Note (MCP 2.x): transport params (host/port) moved from the constructor to run().
mcp = MCPServer("s3-vectors-retrieval")


# ---------------------------------------------------------------------------
# Helper functions (provided)
# ---------------------------------------------------------------------------

def _embed(text: str) -> List[float]:
    """Generate a 1024-dim embedding using Nova Multimodal Embeddings."""
    bedrock = boto3.client('bedrock-runtime')
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({
            'taskType': 'SINGLE_EMBEDDING',
            'singleEmbeddingParams': {
                'embeddingPurpose': 'GENERIC_INDEX',
                'embeddingDimension': EMBED_DIM,
                'text': {'truncationMode': 'END', 'value': text},
            },
        }),
        contentType='application/json',
        accept='application/json',
    )
    return json.loads(resp['body'].read())['embeddings'][0]['embedding']


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors (unused — S3 Vectors handles this)."""
    pass


# ---------------------------------------------------------------------------
# Task 4.1 — Implement the MCP tool for S3 vector search
# ---------------------------------------------------------------------------

@mcp.tool()
def search_financial_docs(query: str, tenant_id: str, top_k: int = 5) -> str:
    """
    Search tenant-scoped financial documents in S3 Vector Buckets.

    Contains invoices, quotes, budgets, cost records, and payment history.
    Use this tool when the user asks about costs, spending, budgets,
    or financial details of maintenance work.

    Tenant isolation is enforced by index-per-tenant: each tenant has a
    dedicated vector index ({tenant_id}-financial-records). The agent
    cannot access another tenant's index.

    Args:
        query: The search query text.
        tenant_id: The tenant identifier (from SaaS identity context).
        top_k: Number of results to return (default: 5).

    Returns:
        JSON string with the top matching chunks and their similarity scores.
    """
    # TODO: Task 4.1 — Implement S3 Vectors query with per-tenant index
    # Hint: Use s3vectors client, index_name = f"{tenant_id}-financial-records"
    #       Call _embed(query) then s3v.query_vectors(...)
    if not tenant_id:
        return json.dumps({"error": "tenant_id is required"})

    index_name = f"{tenant_id}-financial-records"
    if not validate_tenant_index(index_name, tenant_id):
        return json.dumps({"error": "Invalid tenant index"})

    query_embedding = _embed(query)

    s3v = boto3.client('s3vectors')
    response = s3v.query_vectors(
        vectorBucketName=S3_VECTOR_BUCKET,
        indexName=index_name,
        queryVector={'float32': query_embedding},
        topK=top_k,
        returnMetadata=True,
        returnDistance=True,
    )

    results = []
    for vector in response.get('vectors', []):
        metadata = vector.get('metadata', {})
        results.append({
            'text': metadata.get('source_text', ''),
            'score': round(1.0 - vector.get('distance', 0), 4),
            'metadata': metadata,
        })

    return json.dumps({"results": results})

# ---------------------------------------------------------------------------
# Task 4.2 — Implement tenant index validation
# ---------------------------------------------------------------------------

def validate_tenant_index(index_name: str, tenant_id: str) -> bool:
    """
    Validate that a vector index name belongs to the expected tenant.

    Defense-in-depth: even though we construct the index name from tenant_id,
    this check catches any injection or misconfiguration that could cause
    the tool to query another tenant's index.

    Args:
        index_name: The S3 Vectors index name being queried.
        tenant_id: Expected tenant identifier.

    Returns:
        True if the index name matches the tenant's expected pattern.
    """
    # TODO: Task 4.2 — Validate the index name belongs to the correct tenant
    expected_index = f"{tenant_id}-financial-records"
    return index_name == expected_index

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if '--test' in sys.argv:
        # Standalone test mode (direct function call)
        print("=== S3 Vectors MCP Tool - Test Mode ===")
        print(f"Bucket: {S3_VECTOR_BUCKET}")
        for tid in ['tenant-001', 'tenant-002']:
            print(f"\nSearching as {tid}...")
            try:
                result = search_financial_docs(
                    query="How much did the roof repair cost?",
                    tenant_id=tid,
                    top_k=3,
                )
                parsed = json.loads(result)
                print(f"  Results: {len(parsed.get('results', []))}")
            except NotImplementedError as e:
                print(f"  {e}")
                break
    elif '--serve' in sys.argv:
        # Run as MCP server over SSE (for two-terminal verification)
        port = int(os.environ.get('MCP_PORT', '9100'))
        print(f"=== S3 Vectors MCP Server (SSE) ===")
        print(f"Listening on http://localhost:{port}/sse")
        print(f"Press Ctrl+C to stop.\n")
        # MCP 2.x: host/port are passed to run(), not the constructor.
        mcp.run(transport='sse', host="0.0.0.0", port=port)
    else:
        # Run as MCP server (stdio transport — used by Lab 3 agent)
        mcp.run()
