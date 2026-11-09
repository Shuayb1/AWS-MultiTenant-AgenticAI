# SPDX-License-Identifier: MIT-0
"""
agent.py — Agentic Tool Selection (Lambda + API Gateway)
Module 2, Lab 5

Implements the multi-tool agent as a Lambda function behind API Gateway.
Tenant identity flows through the SaaS identity pipeline established in
Module 1:

    Client → JWT (Bearer token)
      → API Gateway → Lambda Authorizer (validates JWT, enriches context)
        → This Lambda (reads tenantId from authorizer context)
          → Agent selects tool → Tool enforces isolation

The LLM decides WHICH tool to call. The framework (authorizer + Lambda event)
decides WHO is calling. The tenant_id cannot be overridden by prompt injection
because it comes from the authorizer context, not from user input.

Tools:
  1. search_property_surveys     — Function calling (@tool), RLS isolation
  2. search_maintenance_records  — Function calling (@tool), Bedrock KB metadata filter
  3. search_financial_docs       — MCP server, prefix scoping isolation

Usage (local):
    python agent.py

Usage (deployed):
    curl -X POST $API_GATEWAY_URL/agent \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"query": "What is the condition of the roof?"}'
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import boto3
from strands import Agent, tool

# Add sibling lab directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / '02-lab2-function-calling-tool'))
sys.path.insert(0, str(Path(__file__).parent.parent / '03-lab3-knowledge-base-tool'))
sys.path.insert(0, str(Path(__file__).parent.parent / '04-lab4-mcp-server-tool'))

from tool_postgresql import search_property_surveys
from tool_opensearch import search_maintenance_records
# Note: search_financial_docs uses @mcp.tool() in tool_s3_vectors.py (for MCP server mode).
# In Lambda, we import the helpers and wrap as a Strands @tool.
import tool_s3_vectors as _s3_mod


@tool
def search_financial_docs(query: str, tenant_id: str, top_k: int = 5) -> str:
    """
    Search tenant-scoped financial documents in S3 Vector Buckets.

    Contains invoices, quotes, budgets, cost records, and payment history.
    Use this tool when the user asks about costs, spending, budgets,
    or financial details of maintenance work.

    Args:
        query: The search query text.
        tenant_id: The tenant identifier (from SaaS identity context).
        top_k: Number of results to return (default: 5).

    Returns:
        JSON string with the top matching chunks and their similarity scores.
    """
    # Delegate to the S3 vectors module logic (same code as Lab 4's MCP tool)
    return _s3_mod.search_financial_docs(query=query, tenant_id=tenant_id, top_k=top_k)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STACK_NAME = os.environ.get('STACK_NAME', 'workshop-infrastructure')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
MODEL_ID = os.environ.get('MODEL_ID', 'us.amazon.nova-lite-v1:0')

# Set default region so any boto3.client() call works without explicit region_name
boto3.setup_default_session(region_name=AWS_REGION)

SYSTEM_PROMPT = """You are a property management assistant for a multi-tenant SaaS platform.
You have access to three knowledge bases, each containing different types of tenant data:

1. search_property_surveys - Physical condition reports (structure, defects, roof, walls, plumbing)
2. search_maintenance_records - Work orders, repair history, contractor details, service dates
3. search_financial_docs - Invoices, quotes, budgets, cost records, payment history

For each question:
- Decide which knowledge base(s) to search based on what information is needed
- Always pass the tenant_id provided in the user's context
- Combine information from multiple sources when the question spans domains
- If no relevant information is found, say so clearly

Never access data for a tenant other than the one specified in the context.
Do not include thinking tags or internal reasoning in your response. Respond directly."""


# ---------------------------------------------------------------------------
# Tenant context extraction from API Gateway authorizer (provided)
# ---------------------------------------------------------------------------

def extract_tenant_context(event: dict) -> Optional[dict]:
    """
    Extract tenant context injected by the Lambda Authorizer.

    The authorizer (Module 1, Lab 1) validates the JWT, enriches the tenant
    context from DynamoDB, and injects it into the requestContext. This
    function reads that context — the tenant_id here is cryptographically
    verified and cannot be spoofed by the caller or the LLM.

    Args:
        event: API Gateway Lambda proxy event.

    Returns:
        Dict with tenantId, tenantTier, tenantStatus, or None if missing.
    """
    request_context = event.get('requestContext', {})
    authorizer = request_context.get('authorizer', {})

    tenant_id = authorizer.get('tenantId', '')
    if not tenant_id:
        return None

    return {
        'tenantId': tenant_id,
        'tier': authorizer.get('tenantTier', ''),
        'status': authorizer.get('tenantStatus', ''),
    }


# ---------------------------------------------------------------------------
# Task 5.1 — Create the agent with all three tools
# ---------------------------------------------------------------------------

def create_agent(tenant_id: str) -> tuple:
    """
    Create a Strands Agent with all three retrieval tools:
    - search_property_surveys (function calling, RLS isolation)
    - search_maintenance_records (function calling, wraps Bedrock KB)
    - search_financial_docs (function calling, S3 prefix scoping)

    All tools run in-process as @tool decorated functions. In Lab 4 you
    built search_financial_docs as an MCP server for local use. Here in
    Lambda, all tools are registered directly — MCP stdio transport is not
    available in serverless environments.

    Args:
        tenant_id: The tenant identifier from the authorizer context.

    Returns:
        Tuple of (Agent, None). Second element is None (no MCP client to clean up).
    """
    # TODO: Task 5.1 — Create agent with all three tools (function calling + MCP)
    raise NotImplementedError("Complete Task 5.1 — create_agent")  # REPLACE THIS LINE


# ---------------------------------------------------------------------------
# Task 5.2 — Lambda handler with authorizer-injected tenant context
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    """
    Lambda function handler for the multi-tool agent.

    Tenant identity flows from the API Gateway Lambda Authorizer — NOT from
    the request body or user prompt. The authorizer already validated the JWT
    and injected tenantId into the requestContext.

    Flow:
        1. Extract tenant context from authorizer (JWT-verified)
        2. Parse the user's query from the request body
        3. Create agent with tenant_id injected by framework
        4. Return the agent's response

    Args:
        event: API Gateway Lambda proxy integration event.
        context: Lambda context object.

    Returns:
        API Gateway response dict with statusCode and body.
    """
    # TODO: Task 5.2 — Extract tenant context, create agent, return response
    raise NotImplementedError("Complete Task 5.2 — lambda_handler")  # REPLACE THIS LINE


# ---------------------------------------------------------------------------
# Entry point (Lambda runtime calls lambda_handler directly)
# ---------------------------------------------------------------------------

