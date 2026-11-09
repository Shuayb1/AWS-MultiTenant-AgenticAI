# SPDX-License-Identifier: MIT-0
"""
DynamoDB Tenant Repository — Downstream Lambda
Module 1, Tenant Isolation Lab 1

This Lambda is deployed behind API Gateway with the Lambda Authorizer from
Tenant Identity & Context Lab 3 as the authorizer. The full request pipeline is:

    User → Cognito (Lab 1) → JWT with tenant_id
         → API Gateway → Lambda Authorizer (Lab 3)
                       → validates JWT, looks up TenantMetadata (Lab 2)
                       → injects tenant context into event['requestContext']['authorizer']
         → This Lambda → reads context, enforces DynamoDB isolation

This function NEVER decodes a JWT or queries TenantMetadata. That work was
already done by the authorizer. Tenant identity arrives pre-validated.

Authorizer context keys injected by generate_policy() in authorizer.py:
    event['requestContext']['authorizer']['tenantId']      — string
    event['requestContext']['authorizer']['tenantTier']    — string
    event['requestContext']['authorizer']['tenantStatus']  — string
    event['requestContext']['authorizer']['tenantContext'] — JSON string (full metadata)

DynamoDB table design (pooled model):
    PK  = tenantId#entityType   ← composite partition key, the isolation boundary
    SK  = entityId
    GSI = TenantEntityIndex (PK: tenantId, SK: entityType)

Supported routes:
    POST   /documents          → create_item   (Task 3.1)
    GET    /documents/{id}     → get_item      (Task 3.2)
    GET    /documents          → query_by_type (Task 3.3)
    PUT    /documents/{id}     → update_item   (Task 3.4)
"""

import json
import os
import boto3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from boto3.dynamodb.conditions import Attr, Key

from token_vending_machine import token_vending_machine, IsolationError  # noqa: F401 — IsolationError re-exported for callers

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME', 'SaaSApplicationData')
AWS_REGION  = os.environ.get('AWS_REGION', 'us-east-1')


# ---------------------------------------------------------------------------
# Step 1 (provided): Extract tenant context from the authorizer-injected event
# ---------------------------------------------------------------------------

def extract_tenant_context(event: Dict) -> Dict:
    """
    Read the tenant context injected by the Lambda Authorizer (Lab 3).

    API Gateway places the authorizer's context dict at:
        event['requestContext']['authorizer']

    The authorizer stored the full tenant metadata as a JSON string under
    'tenantContext' (see generate_policy() in authorizer.py). We parse it
    back here.

    This is the ONLY place tenant identity is read in this Lambda.
    Never call jwt.decode() or query TenantMetadata from a downstream function.

    Args:
        event: The Lambda event dict received from API Gateway

    Returns:
        Tenant context dict with keys: tenantId, tenantName, tier, status,
        features, limits

    Raises:
        ValueError: if tenantId is missing or tenant status is not 'active'
    """
    authorizer = event.get('requestContext', {}).get('authorizer', {})

    tenant_id = authorizer.get('tenantId')
    if not tenant_id:
        raise ValueError("No tenantId in authorizer context — request was not authorised")

    tenant_context = json.loads(authorizer.get('tenantContext', '{}'))

    # The authorizer already validated status, but we double-check defensively
    if authorizer.get('tenantStatus') != 'active':
        raise ValueError(
            f"Tenant {tenant_id} is not active (status={authorizer.get('tenantStatus')})"
        )

    return tenant_context


# ---------------------------------------------------------------------------
# TenantRepository — scoped to a single tenant via authorizer context
# ---------------------------------------------------------------------------

class TenantRepository:
    """
    Multi-tenant DynamoDB data access layer (pooled model).

    Always constructed from the tenant context injected by the Lambda Authorizer,
    never from a raw tenant_id string. This enforces the downstream pattern:
    tenant identity flows JWT → authorizer → here, and is never re-derived.

    The composite partition key  PK = tenantId#entityType  is the isolation
    boundary. A TenantRepository for tenant-002 can never construct the PK
    for tenant-001, making cross-tenant access structurally impossible.
    """

    def __init__(self, tenant_context: Dict, table_name: str = TABLE_NAME):
        tenant_id = tenant_context.get('tenantId')
        if not tenant_id:
            raise ValueError("tenant_context must contain 'tenantId'")

        self.tenant_id     = tenant_id
        self.tenant_tier   = tenant_context.get('tier', 'free')
        self.tenant_status = tenant_context.get('status', 'unknown')
        self.features      = tenant_context.get('features', [])
        self.limits        = tenant_context.get('limits', {})

        # TVM provides tenant-scoped credentials (see Lab 2)
        self.dynamodb = token_vending_machine.get_resource('dynamodb', tenant_context)
        self.table    = self.dynamodb.Table(table_name)

    # ------------------------------------------------------------------
    # Task 3.1 — Write a new item scoped to this tenant
    # ------------------------------------------------------------------
    # TODO: Implement create_item — see lab instructions (Step 3)
    def create_item(self, entity_type: str, entity_id: str, data: Dict) -> Dict:
        """
        Write a new item with a tenant-prefixed composite partition key.

        PK format: tenantId#entityType  (e.g. "tenant-001#DOCUMENT")
        SK:        entityId             (e.g. "doc-001")

        The PK is the isolation boundary — a repository for tenant-002 can
        never write into tenant-001's partition.

        Args:
            entity_type: Item category, e.g. 'DOCUMENT', 'USER'
            entity_id:   Unique ID within the entity type
            data:        Arbitrary payload stored under the 'data' attribute

        Returns:
            The full item dict written to DynamoDB (including PK, SK, timestamps)
        """
        # TODO: Task 3.1 — write item with composite PK tenantId#entityType
        raise NotImplementedError("Complete Task 3.1 — create_item")  # REPLACE THIS LINE
    def get_item(self, entity_type: str, entity_id: str) -> Optional[Dict]:
        """
        Retrieve one item by entity type and ID.

        Constructs PK from self.tenant_id — a repository for tenant-002
        can never retrieve an item belonging to tenant-001.

        Args:
            entity_type: Item category, e.g. 'DOCUMENT'
            entity_id:   Unique ID within the entity type

        Returns:
            The item dict, or None if not found
        """
        # TODO: Task 3.2 — retrieve item by composite PK tenantId#entityType
        raise NotImplementedError("Complete Task 3.2 — get_item")  # REPLACE THIS LINE
    def query_by_type(self, entity_type: str, limit: int = 100) -> List[Dict]:
        """
        Return all items of a given entity type belonging to this tenant.

        Uses KeyConditionExpression on the composite PK so DynamoDB only
        reads this tenant's partition — no cross-tenant data is touched.

        Args:
            entity_type: Category to query, e.g. 'DOCUMENT'
            limit:       Maximum items to return (default 100)

        Returns:
            List of item dicts (may be empty)
        """
        # Pre-implemented: same composite PK pattern as get_item, but with query()
        response = self.table.query(
            KeyConditionExpression=Key('PK').eq(f"{self.tenant_id}#{entity_type}"),
            Limit=limit,
        )
        return response.get('Items', [])
    def update_item(self, entity_type: str, entity_id: str, updates: Dict) -> None:
        """
        Update the 'data' attribute of an existing item.

        Uses a ConditionExpression on the stored tenantId attribute as a
        second isolation layer: DynamoDB rejects the write if the item's
        tenantId does not match self.tenant_id, even if the PK were wrong.

        Args:
            entity_type: Item category, e.g. 'DOCUMENT'
            entity_id:   Unique ID within the entity type
            updates:     New value for the 'data' attribute
        """
        # Pre-implemented: defense-in-depth via ConditionExpression
        # Even if a bug constructed the wrong PK, DynamoDB would reject the write
        # because the stored tenantId wouldn't match self.tenant_id.
        self.table.update_item(
            Key={
                'PK': f"{self.tenant_id}#{entity_type}",
                'SK': entity_id,
            },
            UpdateExpression='SET #d = :data, updatedAt = :ts',
            ConditionExpression=Attr('tenantId').eq(self.tenant_id),
            ExpressionAttributeNames={'#d': 'data'},
            ExpressionAttributeValues={
                ':data': updates,
                ':ts': datetime.now(timezone.utc).isoformat(),
            },
        )
# ---------------------------------------------------------------------------

def lambda_handler(event: Dict, context) -> Dict:
    """
    Downstream Lambda handler for tenant-isolated document CRUD.

    Reads tenant identity exclusively from event['requestContext']['authorizer']
    — the context injected by the Lab 3 Lambda Authorizer. Never decodes a JWT
    or queries TenantMetadata directly.

    API Gateway resource configuration (see template.yaml):
        POST   /documents          → create_item
        GET    /documents/{id}     → get_item
        GET    /documents          → query_by_type
        PUT    /documents/{id}     → update_item
    """
    try:
        # Read pre-validated tenant context from the Lab 3 authorizer
        tenant_context = extract_tenant_context(event)
        repo = TenantRepository(tenant_context)

        method      = event.get('httpMethod', 'GET')
        path        = event.get('path', '/documents')
        path_params = event.get('pathParameters') or {}
        body        = json.loads(event.get('body') or '{}')

        if method == 'POST' and path == '/documents':
            entity_id = body.get('id', f"doc-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
            item = repo.create_item('DOCUMENT', entity_id, body)
            return _response(201, item)

        elif method == 'GET' and 'id' in path_params:
            item = repo.get_item('DOCUMENT', path_params['id'])
            if item is None:
                return _response(404, {'error': 'Not found'})
            return _response(200, item)

        elif method == 'GET':
            items = repo.query_by_type('DOCUMENT')
            return _response(200, {'items': items, 'count': len(items)})

        elif method == 'PUT' and 'id' in path_params:
            repo.update_item('DOCUMENT', path_params['id'], body)
            return _response(200, {'updated': path_params['id']})

        else:
            return _response(400, {'error': 'Unsupported route'})

    except ValueError as e:
        return _response(403, {'error': str(e)})
    except NotImplementedError as e:
        print(f"TODO not completed: {e}")
        return _response(501, {'error': str(e)})
    except Exception as e:
        print(f"Unhandled error: {e}")
        return _response(500, {'error': 'Internal server error'})


def _response(status_code: int, body: Dict) -> Dict:
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body, default=str),
    }


# ---------------------------------------------------------------------------
# Table setup helper
# ---------------------------------------------------------------------------

def create_table_if_not_exists(table_name: str = TABLE_NAME) -> None:
    """
    Create SaaSApplicationData with the pooled-model key schema and GSI.
    Safe to call when the table already exists.
    """
    client = boto3.client('dynamodb', region_name=AWS_REGION)
    try:
        client.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'PK', 'KeyType': 'HASH'},
                {'AttributeName': 'SK', 'KeyType': 'RANGE'},
            ],
            AttributeDefinitions=[
                {'AttributeName': 'PK',         'AttributeType': 'S'},
                {'AttributeName': 'SK',         'AttributeType': 'S'},
                {'AttributeName': 'tenantId',   'AttributeType': 'S'},
                {'AttributeName': 'entityType', 'AttributeType': 'S'},
            ],
            GlobalSecondaryIndexes=[{
                'IndexName': 'TenantEntityIndex',
                'KeySchema': [
                    {'AttributeName': 'tenantId',   'KeyType': 'HASH'},
                    {'AttributeName': 'entityType', 'KeyType': 'RANGE'},
                ],
                'Projection': {'ProjectionType': 'ALL'},
            }],
            BillingMode='PAY_PER_REQUEST',
        )
        client.get_waiter('table_exists').wait(TableName=table_name)
        print(f"Table '{table_name}' created.")
    except client.exceptions.ResourceInUseException:
        print(f"Table '{table_name}' already exists.")



