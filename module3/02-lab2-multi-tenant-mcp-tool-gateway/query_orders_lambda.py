# SPDX-License-Identifier: MIT-0
"""
query_orders_lambda.py — Lambda Target: Tenant-scoped DynamoDB orders
Module 3, Lab 2

This Lambda is registered as a target on the AgentCore Gateway.
The interceptor injects X-Tenant-ID header before this function is invoked.
Uses TVM (TokenVendingMachine) to get credentials scoped to the calling tenant.

Isolation: TVM session tag + IAM LeadingKeys condition on DynamoDB.
Cross-tenant query → AccessDeniedException from IAM.

Env vars:
  TABLE_NAME   — DynamoDB table name (default: SaaSApplicationData)
  TVM_ROLE_ARN — IAM role ARN for TVM credential scoping
  AWS_REGION   — AWS region (default: us-east-1)
"""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

from token_vending_machine import TokenVendingMachine, IsolationError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TABLE_NAME = os.environ.get("TABLE_NAME", "SaaSApplicationData")
TVM_ROLE_ARN = os.environ.get("TVM_ROLE_ARN", "")
REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))

isolation = TokenVendingMachine(role_arn=TVM_ROLE_ARN, region_name=REGION)


# ---------------------------------------------------------------------------
# Task 2.1 — Query orders for the calling tenant
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    """
    Query DynamoDB orders scoped to the calling tenant.

    The interceptor has already:
      - Validated the JWT (via Gateway Cognito authorizer)
      - Extracted tenantId from the JWT claims
      - Propagated x-tenant-id header to this Lambda via Gateway header propagation

    This function:
      1. Reads x-tenant-id from the propagated headers
      2. Gets TVM-scoped credentials for this tenant
      3. Queries DynamoDB with the tenant's partition key prefix
      4. Returns only this tenant's orders

    IAM condition on the TVM role ensures that even if this code
    tried to query another tenant's partition, IAM would deny it.
    """
    limit = event.get("limit", 10)

    try:
        # TODO: Task 2.1 — Read tenant_id from propagated headers (via context),
        #                   get TVM-scoped session, query DynamoDB orders
        # Read tenant_id from propagated headers (via Lambda context.client_context)
        propagated_headers = context.client_context.custom.get("bedrockAgentCorePropagatedHeaders", {})
        tenant_id = propagated_headers.get("x-tenant-id", "")
        if not tenant_id:
            return {"statusCode": 400, "body": json.dumps({"error": "x-tenant-id header missing"})}

        session = isolation.get_session({"tenantId": tenant_id})
        dynamodb = session.resource("dynamodb")
        table = dynamodb.Table(TABLE_NAME)

        result = table.query(
            KeyConditionExpression=Key("PK").eq(f"{tenant_id}#orders"),
            Limit=limit,
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "tenant_id": tenant_id,
                "orders": result.get("Items", []),
            }, default=str),
        }
    except Exception as e:
        import traceback
        print(f"ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e), "type": type(e).__name__}),
        }
