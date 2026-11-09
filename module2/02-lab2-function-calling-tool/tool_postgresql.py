# SPDX-License-Identifier: MIT-0
"""
tool_postgresql.py — Retrieval Tool via Function Calling (Aurora PostgreSQL)
Module 2, Lab 2

Implements a tenant-isolated retrieval tool using the Strands @tool decorator.
The agent calls this tool via function calling. Tenant isolation is enforced
by PostgreSQL Row Level Security (RLS) — the database engine restricts results
to the current tenant automatically.

Pattern: Function Calling (@tool decorator)
  - Tool is a Python function decorated with @tool
  - Agent calls it directly via function calling
  - Tenant context flows as a parameter
  - Isolation logic is inline in the tool function

Usage:
    python tool_postgresql.py
"""

import json
import os
import sys
from typing import List

import boto3
from strands import tool

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
    config = {
        'AURORA_CLUSTER_ARN': os.environ.get('AURORA_CLUSTER_ARN', ''),
        'APP_USER_SECRET_ARN': os.environ.get('APP_USER_SECRET_ARN', ''),
    }
    if not all(config.values()):
        try:
            cfn = boto3.client('cloudformation', region_name=AWS_REGION)
            resp = cfn.describe_stacks(StackName=STACK_NAME)
            outputs = {o['OutputKey']: o['OutputValue'] for o in resp['Stacks'][0].get('Outputs', [])}
            if not config['AURORA_CLUSTER_ARN']:
                config['AURORA_CLUSTER_ARN'] = outputs.get('AuroraClusterArn', '')
            if not config['APP_USER_SECRET_ARN']:
                config['APP_USER_SECRET_ARN'] = outputs.get('AppUserSecretArn', outputs.get('DatabaseSecretArn', ''))
        except Exception as e:
            print(f"Warning: Could not resolve CFN outputs: {e}")
    return config


_CFG = _resolve_config()
AURORA_CLUSTER_ARN = _CFG['AURORA_CLUSTER_ARN']
APP_USER_SECRET_ARN = _CFG['APP_USER_SECRET_ARN']
DATABASE = os.environ.get('AURORA_DB_NAME', 'saasdb')

# Pre-configured clients (region set once, reused by all functions)
RDS_DATA = boto3.client('rds-data', region_name=AWS_REGION)
BEDROCK = boto3.client('bedrock-runtime', region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Helper functions (provided)
# ---------------------------------------------------------------------------

def _embed(text: str) -> List[float]:
    """Generate a 1024-dim embedding using Nova Multimodal Embeddings."""
    resp = BEDROCK.invoke_model(
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


# ---------------------------------------------------------------------------
# Task 2.1 — Set the RLS session variable
# ---------------------------------------------------------------------------


def _set_tenant_session(rds_data, tenant_id: str) -> str:
    """
    Activate RLS for the current connection by setting the PostgreSQL
    session variable that the RLS policy evaluates.

    Must be called before every query on property_mgmt.property_surveys.
    Uses set_config() rather than SET because SET does not accept bind
    parameters via the RDS Data API.

    Opens an explicit transaction so the session variable persists across
    subsequent execute_statement calls. Returns the transactionId which
    must be passed to all subsequent statements and committed when done.

    Args:
        rds_data: boto3 rds-data client.
        tenant_id: tenant identifier (e.g. 'tenant-001').

    Returns:
        transactionId to use in subsequent execute_statement calls.
    """
    # TODO: Task 2.1 — Set the RLS session variable for tenant isolation
    raise NotImplementedError("Complete Task 2.1 — _set_tenant_session")  # REPLACE THIS LINE


# ---------------------------------------------------------------------------
# Task 2.2 — Implement the @tool decorated retrieval function
# ---------------------------------------------------------------------------

@tool
def search_property_surveys(query: str, tenant_id: str, top_k: int = 3) -> str:
    """
    Search tenant-scoped property survey reports in Aurora PostgreSQL.

    Contains structural condition data: roof condition, wall defects,
    plumbing status, electrical assessments, and surveyor recommendations.
    Use this tool when the user asks about physical property condition.

    Tenant isolation is enforced by Row Level Security (RLS). The database
    automatically filters results to only return chunks belonging to the
    specified tenant.

    Args:
        query: The search query text to find similar documents.
        tenant_id: The tenant identifier from the SaaS identity context.
        top_k: Number of results to return (default: 3).

    Returns:
        JSON string with matching chunks, scores, and metadata.
    """
    # TODO: Task 2.2 — Implement the retrieval tool with RLS isolation
    # Hint: Use the module-level RDS_DATA client and _embed() helper
    raise NotImplementedError("Complete Task 2.2 — search_property_surveys")  # REPLACE THIS LINE


# ---------------------------------------------------------------------------
# Demo / verification
# ---------------------------------------------------------------------------

def main():
    if not AURORA_CLUSTER_ARN or not APP_USER_SECRET_ARN:
        print("ERROR: Cannot resolve Aurora config. Check stack deployment.")
        sys.exit(1)

    question = "What is the condition of the roof?"

    for tenant_id in ['tenant-001', 'tenant-002']:
        print(f"\n=== {tenant_id}: {question} ===")
        try:
            result_json = search_property_surveys(
                query=question,
                tenant_id=tenant_id,
                top_k=3,
            )
            result = json.loads(result_json)
            print(f"Results: {len(result.get('results', []))} chunks")
            for r in result.get('results', [])[:2]:
                print(f"  Score: {r['score']:.4f} | {r['text'][:80]}...")
        except NotImplementedError as e:
            print(f"  {e}")
            return

    print("\n=== Isolation proof ===")
    print("RLS ensures tenant-002 cannot see tenant-001's data (no WHERE clause needed)")


if __name__ == '__main__':
    main()
