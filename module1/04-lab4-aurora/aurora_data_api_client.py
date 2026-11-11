# SPDX-License-Identifier: MIT-0
"""
AuroraDataAPIClient — Tenant-Isolated Aurora PostgreSQL via RDS Data API
Module 1, Lab 6

Implements tenant isolation using PostgreSQL Row-Level Security (RLS) with
the RDS Data API. Isolation is enforced at the database level — not the
application layer.

Isolation model:
    1. A single shared app_user connects via Secrets Manager (database auth).
    2. Before every query, set_config('app.current_tenant', tenantId, false)
       is called within an explicit transaction to bind the session variable.
       Note: the PostgreSQL SET command does not accept bind parameters;
       set_config() is used instead.
    3. PostgreSQL RLS policy evaluates current_setting('app.current_tenant')
       on every row — the database enforces the boundary, not the application.
    4. Because RDS Data API calls are stateless (no guaranteed connection
       reuse), an explicit transaction is required to hold the set_config call
       and the query on the same connection.

Why not STS/TVM here:
    The RDS Data API authenticates using the Secrets Manager secret (database
    user credentials). STS session tags have no effect on what the rds-data
    service does with that secret — scoping the boto3 client per-tenant via
    AssumeRole provides no database-level isolation. RLS + session variables
    is the correct mechanism for pooled Aurora multi-tenancy.

Usage:
    from aurora_data_api_client import AuroraDataAPIClient

    client = AuroraDataAPIClient(tenant_context)
    result = client.execute_statement(
        "SELECT * FROM documents WHERE id = :id",
        parameters=[{'name': 'id', 'value': {'stringValue': 'doc-001'}}],
    )
"""

import os
import boto3
from typing import Dict, List


# ---------------------------------------------------------------------------
# AuroraIsolationError
# ---------------------------------------------------------------------------

class AuroraIsolationError(Exception):
    """
    Raised when tenant context is missing or an RDS Data API call fails.
    Never falls back silently — callers must handle this explicitly.
    """


# ---------------------------------------------------------------------------
# AuroraDataAPIClient
# ---------------------------------------------------------------------------

class AuroraDataAPIClient:
    """
    Executes SQL against Aurora PostgreSQL via the RDS Data API with
    database-level tenant isolation enforced by PostgreSQL RLS.

    Tenant identity flows JWT → Lambda Authorizer → tenant_context → here.
    It is never re-derived from the request body or query parameters.

    Every execute_statement call:
      1. Opens an explicit transaction (required for stateless Data API)
      2. Sets app.current_tenant session variable to the tenant ID
      3. Executes the SQL (RLS policy filters rows automatically)
      4. Commits the transaction
    """

    def __init__(self, tenant_context: Dict) -> None:
        """
        Initialise the client using the shared app_user Secrets Manager secret
        and Aurora cluster ARN from environment variables.

        Args:
            tenant_context: Dict with at least 'tenantId', injected by the
                            Lambda Authorizer — never trust caller-supplied values.

        Raises:
            AuroraIsolationError: if tenantId is absent, or required env vars
                                  are missing.
        """
        tenant_id = tenant_context.get('tenantId')
        if not tenant_id:
            raise AuroraIsolationError(
                "tenant_context must contain a non-empty 'tenantId'."
            )
        self.tenant_id = tenant_id

        cluster_arn = os.environ.get('AURORA_CLUSTER_ARN', '')
        secret_arn  = os.environ.get('AURORA_SECRET_ARN', '')

        if not cluster_arn:
            raise AuroraIsolationError(
                "AURORA_CLUSTER_ARN environment variable is not set. "
                "Ensure the CloudFormation stack has EnableHttpEndpoint: true "
                "on the AuroraCluster resource."
            )
        if not secret_arn:
            raise AuroraIsolationError(
                "AURORA_SECRET_ARN environment variable is not set. "
                "This secret must belong to the app_user database role — "
                "NOT the cluster admin/owner role, which bypasses RLS."
            )

        self.cluster_arn = cluster_arn
        self.secret_arn  = secret_arn
        self.database    = os.environ.get('AURORA_DB_NAME', 'saasdb')

        # Single shared rds-data client — isolation is enforced by RLS, not IAM.
        self._rds_data = boto3.client('rds-data')

    # ------------------------------------------------------------------
    # Internal: set tenant context and execute within a transaction
    # ------------------------------------------------------------------

    def _execute_in_tenant_context(
        self, sql: str, parameters: List[Dict]
    ) -> Dict:
        """
        Execute SQL within an explicit transaction that first sets the
        app.current_tenant session variable, activating the RLS policy.

        An explicit transaction is required because RDS Data API calls are
        stateless — without it, the set_config call and the query may land on
        different connections and the session variable would be lost.

        Args:
            sql:        SQL string with named parameters (e.g. :id, :title).
                        Do NOT include :tenantId — RLS handles filtering.
            parameters: RDS Data API parameter list for the SQL query.

        Returns:
            Raw ExecuteStatement response dict from the RDS Data API.

        Raises:
            AuroraIsolationError: on any RDS Data API failure.
        """
        tr = None
        try:
            # TODO: Task 4.2 — begin a transaction, set app.current_tenant,
            # execute the query, commit, and return the response.
            #
            # Step 1: begin_transaction (required — Data API is stateless)
            # Step 2: set_config('app.current_tenant', tenantId, false)
            #         — activates the RLS policy for this transaction
            # Step 3: execute the actual query (RLS filters rows automatically)
            # Step 4: commit_transaction
            # Step 1: open a transaction
            tr = self._rds_data.begin_transaction(
                resourceArn=self.cluster_arn,
                secretArn=self.secret_arn,
                database=self.database,
            )

            # Step 2: set the session variable using set_config()
            # (SET command doesn't accept bind params; set_config() does)
            self._rds_data.execute_statement(
                resourceArn=self.cluster_arn,
                secretArn=self.secret_arn,
                database=self.database,
                sql="SELECT set_config('app.current_tenant', :tenantId, false)",
                parameters=[{'name': 'tenantId', 'value': {'stringValue': self.tenant_id}}],
                transactionId=tr['transactionId'],
            )

            # Step 3: execute the actual query - RLS now filters rows automatically
            response = self._rds_data.execute_statement(
                resourceArn=self.cluster_arn,
                secretArn=self.secret_arn,
                database=self.database,
                sql=sql,
                parameters=parameters,
                transactionId=tr['transactionId'],
            )

            # Step 4: commit
            self._rds_data.commit_transaction(
                resourceArn=self.cluster_arn,
                secretArn=self.secret_arn,
                transactionId=tr['transactionId'],
            )

            return response
        except AuroraIsolationError:
            raise
        except Exception as exc:
            # Roll back if we opened a transaction before the failure
            if tr:
                try:
                    self._rds_data.rollback_transaction(
                        resourceArn=self.cluster_arn,
                        secretArn=self.secret_arn,
                        transactionId=tr['transactionId'],
                    )
                except Exception:
                    pass
            raise AuroraIsolationError(
                f"RDS Data API call failed for tenant '{self.tenant_id}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Public: execute a single SQL statement
    # ------------------------------------------------------------------

    def execute_statement(self, sql: str, parameters: List[Dict]) -> Dict:
        """
        Execute a single SQL statement with RLS tenant isolation active.

        The RLS policy on the table automatically filters rows to the current
        tenant — callers do NOT need to include WHERE tenant_id = ... in their
        SQL, though it is still good practice as a defence-in-depth measure.

        Args:
            sql:        SQL string with named parameters (e.g. :id, :title).
            parameters: RDS Data API parameter list — do NOT include tenantId,
                        that is handled by the session variable.

        Returns:
            Raw ExecuteStatement response dict from the RDS Data API.

        Raises:
            AuroraIsolationError: on any RDS Data API failure.
        """
        return self._execute_in_tenant_context(sql, parameters)

    # ------------------------------------------------------------------
    # Public: batch execute using stored function (Task 4.3)
    # ------------------------------------------------------------------

    def execute_as_tenant(self, sql: str, parameters: List[Dict]) -> Dict:
        """
        Alternative single-call approach using a PostgreSQL stored function
        that wraps SET + query atomically — no explicit transaction needed.

        The get_as_tenant(p_tenant_id, p_sql) function must exist in the DB
        (created in Task 4.1 schema setup). This avoids begin/commit boilerplate
        and is the preferred pattern when the query shape is fixed.

        Args:
            sql:        The inner SQL to execute inside the function.
            parameters: RDS Data API parameter list for the inner SQL.

        Returns:
            Raw ExecuteStatement response dict from the RDS Data API.

        Raises:
            AuroraIsolationError: on any RDS Data API failure.
        """
        try:
            # Stored function wraps SET + query atomically — no transaction needed.
            response = self._rds_data.execute_statement(
                resourceArn=self.cluster_arn,
                secretArn=self.secret_arn,
                database=self.database,
                sql="SELECT execute_as_tenant(:tenantId, :query)",
                parameters=[
                    {'name': 'tenantId', 'value': {'stringValue': self.tenant_id}},
                    {'name': 'query',    'value': {'stringValue': sql}},
                ],
            )
            return response

        except AuroraIsolationError:
            raise
        except Exception as exc:
            raise AuroraIsolationError(
                f"execute_as_tenant failed for tenant '{self.tenant_id}': {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Lambda handler — entry point wired to API Gateway
# ---------------------------------------------------------------------------

def extract_tenant_context(event: Dict) -> Dict:
    """
    Read the tenant context injected by the Lambda Authorizer (Lab 3).

    Tenant identity flows JWT → authorizer → here; it is never re-derived.

    Raises:
        ValueError: if tenantId is missing or tenant status is not 'active'.
    """
    import json as _json
    authorizer = event.get('requestContext', {}).get('authorizer', {})

    tenant_id = authorizer.get('tenantId')
    if not tenant_id:
        raise ValueError("No tenantId in authorizer context — request was not authorised")

    tenant_context = _json.loads(authorizer.get('tenantContext', '{}'))

    if authorizer.get('tenantStatus') != 'active':
        raise ValueError(
            f"Tenant {tenant_id} is not active (status={authorizer.get('tenantStatus')})"
        )

    return tenant_context


def lambda_handler(event: Dict, context) -> Dict:
    """
    Lambda handler for tenant-isolated Aurora document CRUD via RDS Data API.

    Reads tenant identity exclusively from event['requestContext']['authorizer'].
    Never decodes a JWT or queries TenantMetadata directly.

    Supported routes:
        POST   /aurora/documents       → insert document row
        GET    /aurora/documents/{id}  → select document by id
        GET    /aurora/documents       → list all documents for tenant
        DELETE /aurora/documents/{id}  → delete document by id
    """
    import json as _json

    try:
        tenant_context = extract_tenant_context(event)
        client = AuroraDataAPIClient(tenant_context)

        method      = event.get('httpMethod', 'GET')
        path_params = event.get('pathParameters') or {}
        body        = _json.loads(event.get('body') or '{}')

        if method == 'POST':
            doc_id  = body.get('id', f"doc-{__import__('datetime').datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
            title   = body.get('title', '')
            content = body.get('content', '')
            result = client.execute_statement(
                # tenant_id column is populated from the session variable via a
                # BEFORE INSERT trigger or explicitly here as defence-in-depth.
                "INSERT INTO documents (id, tenant_id, title, content, created_at) "
                "VALUES (:id, current_setting('app.current_tenant'), :title, :content, NOW()) "
                "ON CONFLICT (id, tenant_id) DO UPDATE SET title=:title, content=:content "
                "RETURNING id, tenant_id, title",
                parameters=[
                    {'name': 'id',      'value': {'stringValue': doc_id}},
                    {'name': 'title',   'value': {'stringValue': title}},
                    {'name': 'content', 'value': {'stringValue': content}},
                ],
            )
            return _response(201, {'id': doc_id, 'tenantId': tenant_context['tenantId'], 'result': result})

        elif method == 'GET' and 'id' in path_params:
            result = client.execute_statement(
                # RLS filters to current tenant automatically.
                # WHERE id = :id is the only business filter needed.
                "SELECT id, tenant_id, title, content, created_at "
                "FROM documents WHERE id = :id",
                parameters=[
                    {'name': 'id', 'value': {'stringValue': path_params['id']}},
                ],
            )
            records = result.get('records', [])
            if not records:
                return _response(404, {'error': 'Not found'})
            return _response(200, {'records': records})

        elif method == 'GET':
            result = client.execute_statement(
                # No WHERE clause needed — RLS restricts to current tenant rows.
                "SELECT id, tenant_id, title, created_at "
                "FROM documents ORDER BY created_at DESC LIMIT 100",
                parameters=[],
            )
            records = result.get('records', [])
            return _response(200, {'items': records, 'count': len(records)})

        elif method == 'DELETE' and 'id' in path_params:
            client.execute_statement(
                "DELETE FROM documents WHERE id = :id",
                parameters=[
                    {'name': 'id', 'value': {'stringValue': path_params['id']}},
                ],
            )
            return _response(200, {'deleted': path_params['id']})

        else:
            return _response(400, {'error': 'Unsupported route'})

    except ValueError as e:
        return _response(403, {'error': str(e)})
    except AuroraIsolationError as e:
        print(f"AuroraIsolationError: {e}")
        return _response(500, {'error': 'Tenant isolation failure'})
    except Exception as e:
        print(f"Unhandled error: {e}")
        return _response(500, {'error': 'Internal server error'})


def _response(status_code: int, body: Dict) -> Dict:
    import json as _json
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'application/json'},
        'body': _json.dumps(body, default=str),
    }
