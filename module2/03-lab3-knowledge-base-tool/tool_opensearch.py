# SPDX-License-Identifier: MIT-0
"""
tool_opensearch.py — Retrieval Tool via Bedrock Knowledge Base (OpenSearch)
Module 2, Lab 3

Implements a tenant-isolated retrieval tool using Amazon Bedrock Knowledge Bases.
The KB is backed by OpenSearch Serverless and handles chunking, embedding, and
retrieval as a managed service. Tenant isolation is enforced via metadata filtering.

Pattern: Bedrock Knowledge Base
  - AWS manages the vector store, embedding, and retrieval
  - Agent calls the KB Retrieve API
  - Tenant isolation via metadata filter on tenant_id field
  - Trade-off: less control, but zero infrastructure to manage

Usage:
    python tool_opensearch.py
"""

import json
import os
import sys
from typing import Dict, List

import boto3
from strands import tool

# ---------------------------------------------------------------------------
# Configuration (auto-resolved from CloudFormation)
# ---------------------------------------------------------------------------

STACK_NAME = os.environ.get('STACK_NAME', 'workshop-infrastructure')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID', '')

# Set default region so any boto3.client() call works without explicit region_name
boto3.setup_default_session(region_name=AWS_REGION)


def _resolve_config():
    config = {'KNOWLEDGE_BASE_ID': os.environ.get('KNOWLEDGE_BASE_ID', '')}
    if not config['KNOWLEDGE_BASE_ID']:
        try:
            cfn = boto3.client('cloudformation', region_name=AWS_REGION)
            resp = cfn.describe_stacks(StackName=STACK_NAME)
            outputs = {o['OutputKey']: o['OutputValue'] for o in resp['Stacks'][0].get('Outputs', [])}
            config['KNOWLEDGE_BASE_ID'] = outputs.get('KnowledgeBaseId', '')
        except Exception as e:
            print(f"Warning: Could not resolve CFN outputs: {e}")
    return config


_CFG = _resolve_config()
KNOWLEDGE_BASE_ID = _CFG['KNOWLEDGE_BASE_ID']


# ---------------------------------------------------------------------------
# Task 3.1 — Implement the @tool decorated KB retrieval function
# ---------------------------------------------------------------------------

@tool
def search_maintenance_records(query: str, tenant_id: str, top_k: int = 3) -> str:
    """
    Search tenant-scoped maintenance history via Bedrock Knowledge Base.

    Contains work orders, repair records, contractor details, and service dates.
    Use this tool when the user asks about maintenance history, repairs,
    or when work was last performed.

    The Knowledge Base is backed by OpenSearch Serverless. Bedrock handles
    embedding the query and performing the vector search. Tenant isolation
    is enforced by a metadata filter that restricts results to documents
    where metadata.tenant_id matches the specified tenant.

    Args:
        query: The search query text to find similar documents.
        tenant_id: The tenant identifier from the SaaS identity context.
        top_k: Number of results to return (default: 3).

    Returns:
        JSON string with matching chunks, scores, and metadata.
    """
    # TODO: Task 3.1 — Call Bedrock KB Retrieve API with tenant_id metadata filter
    if not tenant_id:
        return json.dumps({"error": "tenant_id is required"})

    client = boto3.client('bedrock-agent-runtime')
    tenant_filter = build_tenant_filter(tenant_id)

    response = client.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={'text': query},
        retrievalConfiguration={
            'vectorSearchConfiguration': {
                'numberOfResults': top_k,
                'filter': tenant_filter,
            }
        },
    )

    results = []
    for item in response.get('retrievalResults', []):
        results.append({
            'text': item['content']['text'],
            'score': item.get('score', 0),
            'metadata': item.get('metadata', {}),
        })
    return json.dumps({"results": results})

# ---------------------------------------------------------------------------
# Task 3.2 — Implement the metadata filter builder
# ---------------------------------------------------------------------------

def build_tenant_filter(tenant_id: str) -> Dict:
    """
    Build a Bedrock KB retrieval filter that scopes results to a single tenant.

    The filter uses the 'stringContains' operator on the source URI field.
    Documents are stored under s3://{bucket}/kb-datasource/{tenant_id}/,
    so filtering the source URI for the tenant_id path segment ensures
    only that tenant's documents are returned.

    Args:
        tenant_id: The tenant identifier to filter on.

    Returns:
        Filter dict in Bedrock KB RetrievalFilter format.
    """
    # TODO: Task 3.2 — Build the metadata filter for tenant isolation
    return {
        'stringContains': {
            'key': 'x-amz-bedrock-kb-source-uri',
            'value': f'/kb-datasource/{tenant_id}/',
        }
    }

# ---------------------------------------------------------------------------
# Demo / verification
# ---------------------------------------------------------------------------

def main():
    if not KNOWLEDGE_BASE_ID:
        print("ERROR: KNOWLEDGE_BASE_ID not set. Create a KB or set the env var.")
        print("  export KNOWLEDGE_BASE_ID=<your-kb-id>")
        sys.exit(1)

    question = "When was the HVAC last serviced?"

    for tenant_id in ['tenant-001', 'tenant-002']:
        print(f"\n=== {tenant_id}: {question} ===")
        try:
            result_json = search_maintenance_records(
                query=question,
                tenant_id=tenant_id,
                top_k=3,
            )
            result = json.loads(result_json)
            print(f"Results: {len(result.get('results', []))} chunks")
            for r in result.get('results', [])[:2]:
                print(f"  Score: {r.get('score', 'N/A')} | {r['text']}")
        except NotImplementedError as e:
            print(f"  {e}")
            return

    print("\n=== Isolation proof ===")
    print("Metadata filter ensures tenant-002 only sees their own documents")


if __name__ == '__main__':
    main()
