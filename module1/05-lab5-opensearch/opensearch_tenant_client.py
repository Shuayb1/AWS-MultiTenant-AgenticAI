# SPDX-License-Identifier: MIT-0
"""
OpenSearch Tenant Client - Index-per-Tenant Silo Model
Module 1, Lab 5

Implements tenant isolation for Amazon OpenSearch Serverless using the
index-per-tenant (silo) model. Each tenant gets a dedicated index named
{tenantId}-documents (e.g. tenant-001-documents). The FGAC data access
policy restricts the TVM-scoped role to indexes matching tenant-*.

Two layers of isolation:
  1. Index naming: code constructs {tenantId}-documents (e.g. tenant-001-documents),
     so a client for tenant-002 can never reference tenant-001's index.
  2. FGAC policy: even if the index name were guessed, the IAM condition on
     aws:PrincipalTag/tenantId rejects the request at the AWS control plane.

Integration with Labs 1 and 2:
  - Lab 1 (SaaS Identity): tenant_context provides tenantId and tier
  - Lab 2 (Token Vending Machine): provides scoped credentials with tenantId
    session tag that FGAC evaluates

Usage:
    from opensearch_tenant_client import TenantOpenSearchClient

    client = TenantOpenSearchClient(tenant_context)
    client.index_document('doc-001', 'Q4 Report', 'Full text...')
    results = client.search_documents('quarterly report')
"""

import os
from typing import Dict, List, Optional

from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

from token_vending_machine import token_vending_machine, IsolationError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_ENDPOINT = os.environ.get('OPENSEARCH_ENDPOINT', '')
_AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
_SERVICE = 'aoss'  # Amazon OpenSearch Serverless


# ---------------------------------------------------------------------------
# TenantOpenSearchClient - Index-per-Tenant Silo Model
# ---------------------------------------------------------------------------

class TenantOpenSearchClient:
    """
    Tenant-scoped OpenSearch Serverless client using the index-per-tenant
    silo model.

    Each tenant gets a dedicated index: {tenantId}-documents
    (e.g. tenant-001-documents, tenant-002-documents).
    The FGAC data access policy on the collection restricts the assumed
    role to indexes matching tenant-* only.

    This client never accepts an index name as a parameter, preventing
    callers from targeting another tenant's index.
    """

    def __init__(self, tenant_context: Dict, endpoint: Optional[str] = None):
        """
        Obtain tenant-scoped credentials from the TVM (Lab 2) and build
        an AWS4Auth-signed OpenSearch client.

        Args:
            tenant_context: Dict with tenantId from Lab 1 authorizer context.
            endpoint: OpenSearch Serverless collection endpoint.

        Raises:
            IsolationError: if tenantId is absent or TVM fails.
        """
        tenant_id = tenant_context.get('tenantId')
        if not tenant_id:
            raise IsolationError("tenant_context must contain a non-empty 'tenantId'.")
        self.tenant_id = tenant_id

        _endpoint = endpoint or _DEFAULT_ENDPOINT
        if not _endpoint:
            raise IsolationError("OPENSEARCH_ENDPOINT not configured.")

        # TODO: Task 5.1 - Get scoped credentials from TVM and build AWS4Auth
        raise NotImplementedError("Complete Task 5.1 - get credentials and build AWS4Auth")  # REPLACE THIS LINE

        # TODO: Task 5.2 - Set the tenant-scoped index name
        raise NotImplementedError("Complete Task 5.2 - set index name")  # REPLACE THIS LINE

        host = _endpoint.replace('https://', '').replace('http://', '').rstrip('/')
        self._client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    def index_document(self, document_id: str, title: str, content: str) -> Dict:
        """
        Index a document into the tenant's dedicated index.

        The tenant_id field is included as metadata but is not the isolation
        mechanism here. Isolation is structural (index name) + FGAC.
        """
        # TODO: Task 5.3 - Index a document into the tenant's dedicated index
        raise NotImplementedError("Complete Task 5.3 - index_document")  # REPLACE THIS LINE

    def search_documents(self, query: str, size: int = 10) -> List[Dict]:
        """
        Full-text search within the tenant's dedicated index.

        No tenant filter is needed because the index itself is the isolation
        boundary. Only this tenant's documents exist in this index.
        """
        # TODO: Task 5.4 - Search within the tenant's dedicated index
        raise NotImplementedError("Complete Task 5.4 - search_documents")  # REPLACE THIS LINE

    def get_document(self, document_id: str) -> Optional[Dict]:
        """Get a document by ID from the tenant's dedicated index."""
        try:
            response = self._client.get(index=self.index_name, id=document_id)
            return response['_source']
        except Exception:
            return None

    def delete_document(self, document_id: str) -> None:
        """Delete a document from the tenant's dedicated index."""
        try:
            self._client.delete(index=self.index_name, id=document_id)
        except Exception as exc:
            raise IsolationError(
                f"delete_document failed for tenant '{self.tenant_id}': {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

import json


def lambda_handler(event: Dict, context) -> Dict:
    """
    API Gateway Lambda Proxy handler for OpenSearch operations.

    Routes:
      POST /opensearch/documents       → index_document
      GET  /opensearch/documents?q=...  → search_documents
      GET  /opensearch/documents/{id}   → get_document
    """
    method = event.get('httpMethod', 'GET')
    path = event.get('path', '')
    authorizer = event.get('requestContext', {}).get('authorizer', {})
    tenant_id = authorizer.get('tenantId', '')

    if not tenant_id:
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'tenantId not found in authorizer context'}),
        }

    tenant_context = {'tenantId': tenant_id}

    try:
        client = TenantOpenSearchClient(tenant_context)
    except (IsolationError, NotImplementedError) as e:
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': f'Tenant isolation failure: {str(e)}'}),
        }

    try:
        if method == 'POST':
            body = json.loads(event.get('body', '{}'))
            result = client.index_document(
                document_id=body.get('id', ''),
                title=body.get('title', ''),
                content=body.get('content', ''),
            )
            return {
                'statusCode': 201,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'id': body.get('id'), 'tenantId': tenant_id, 'index': client.index_name}),
            }

        elif method == 'GET':
            # Check for query parameter (search) vs path parameter (get by ID)
            query_params = event.get('queryStringParameters') or {}
            path_params = event.get('pathParameters') or {}

            if query_params.get('q'):
                results = client.search_documents(query_params['q'])
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'items': results, 'index': client.index_name}),
                }
            elif path_params.get('id'):
                doc = client.get_document(path_params['id'])
                if doc:
                    return {
                        'statusCode': 200,
                        'headers': {'Content-Type': 'application/json'},
                        'body': json.dumps(doc),
                    }
                else:
                    return {
                        'statusCode': 404,
                        'headers': {'Content-Type': 'application/json'},
                        'body': json.dumps({'error': 'Not found'}),
                    }
            else:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'error': 'Provide ?q=search_term or a document ID in the path'}),
                }

    except IsolationError as e:
        return {
            'statusCode': 403,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)}),
        }
    except NotImplementedError as e:
        return {
            'statusCode': 501,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)}),
        }
