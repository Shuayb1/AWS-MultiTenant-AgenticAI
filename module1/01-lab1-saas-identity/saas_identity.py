# SPDX-License-Identifier: MIT-0
"""
SaaS Identity — Lab 1
Implements the SaaS identity pipeline: user authentication, tenant context
enrichment, and authorizer decision logic.

SaaS identity merges user identity with tenant identity into a single token
that flows through every layer of the system. This binding provides the tenant
context used to enforce isolation, apply tier-based policies, and route requests.

Reference:
- https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/saas-identity.html
- https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/saas-identity.html
"""

import boto3
import json
import jwt
import os
import urllib.request
import urllib.error
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Configuration — sourced from environment variables (never hardcoded)
# ---------------------------------------------------------------------------
USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')


class SaaSIdentityService:
    """
    Implements the SaaS identity pipeline:
    1. Authenticate user → receive JWT with embedded tenant identity
    2. Extract tenant_id from the token (SaaS identity binding)
    3. Enrich with full tenant context from DynamoDB
    4. Enforce access and propagate context to downstream services
    """

    def __init__(self, user_pool_id: str = USER_POOL_ID, client_id: str = CLIENT_ID):
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.cognito_client = boto3.client('cognito-idp', region_name=AWS_REGION)
        self.dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        self.table = self.dynamodb.Table('TenantMetadata')

    # ------------------------------------------------------------------
    # Pre-implemented: Authenticate user with Cognito
    # ------------------------------------------------------------------
    def authenticate_user(self, username: str, password: str) -> Optional[Dict]:
        """
        Authenticate against Cognito using USER_PASSWORD_AUTH flow.
        Returns AuthenticationResult (IdToken, AccessToken, RefreshToken) or None.
        """
        try:
            response = self.cognito_client.initiate_auth(
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={
                    'USERNAME': username,
                    'PASSWORD': password,
                },
                ClientId=self.client_id,
            )
            return response['AuthenticationResult']
        except Exception as e:
            print(f"Authentication error: {e}")
            return None

    # ------------------------------------------------------------------
    # Pre-implemented: Decode the JWT ID token
    # ------------------------------------------------------------------
    def decode_id_token(self, id_token: str) -> Optional[Dict]:
        """
        Decode the JWT ID token to inspect its claims.
        NOTE: verify_signature=False is for workshop use only.
        In production, always verify against Cognito's JWKS endpoint.
        """
        try:
            decoded = jwt.decode(
                id_token,
                options={"verify_signature": False},
                algorithms=["RS256", "HS256"],
            )
            return decoded
        except Exception as e:
            print(f"Token decode error: {e}")
            return None

    # ------------------------------------------------------------------
    # Task 1.1: Extract the SaaS identity (tenant_id) from the JWT
    # ------------------------------------------------------------------
    def extract_tenant_id(self, decoded_token: Dict) -> Optional[str]:
        """
        Extract 'custom:tenant_id' from decoded JWT claims.

        This is the SaaS identity binding point — the tenant_id is embedded
        in the token at user creation time and cannot be spoofed by the caller.

        Args:
            decoded_token: Decoded JWT claims dictionary

        Returns:
            Tenant ID string or None if not present
        """
        # TODO: Task 1.1 — Extract the SaaS identity (tenant_id) from the decoded JWT claims
        raise NotImplementedError("Complete Task 1.1")  # REPLACE THIS LINE

    # ------------------------------------------------------------------
    # Task 1.2: Retrieve tenant context from DynamoDB
    # ------------------------------------------------------------------
    def get_tenant_context(self, tenant_id: str) -> Optional[Dict]:
        """
        Retrieve full tenant context from DynamoDB.

        A bare tenant_id tells you WHICH tenant. The context tells you
        WHAT KIND of tenant — their tier, status, feature entitlements,
        and limits. This drives all downstream multi-tenant policies.

        Args:
            tenant_id: The tenant identifier extracted from the JWT

        Returns:
            Full tenant metadata dict or None if not found
        """
        # TODO: Task 1.2 — Retrieve full tenant context from DynamoDB
        raise NotImplementedError("Complete Task 1.2")  # REPLACE THIS LINE

    # ------------------------------------------------------------------
    # Task 1.3: Authorizer decision logic
    # ------------------------------------------------------------------
    def authorize_request(self, decoded_token: Dict) -> Optional[Dict]:
        """
        The authorizer decision logic — the enforcement point of SaaS identity.

        This method:
        1. Extracts tenant_id from the decoded JWT (SaaS identity binding)
        2. Enriches with full tenant context from DynamoDB
        3. Validates tenant is active (fail-closed)
        4. Returns enriched context for propagation to downstream services

        Args:
            decoded_token: Decoded JWT claims dictionary

        Returns:
            Enriched tenant context dict if authorized, None if denied
        """
        # TODO: Task 1.3 — Implement the authorizer decision logic
        raise NotImplementedError("Complete Task 1.3")  # REPLACE THIS LINE

    # ------------------------------------------------------------------
    # Pre-implemented: Call a protected API endpoint
    # ------------------------------------------------------------------
    def call_protected_api(self, api_url: str, id_token: str, path: str = '/resource') -> Dict:
        """
        Call a protected API Gateway endpoint using Authorization: Bearer <token>.
        Returns dict with 'status_code' and 'body'.
        """
        url = api_url.rstrip('/') + path
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {id_token}'})
        try:
            with urllib.request.urlopen(req) as resp:
                return {'status_code': resp.status, 'body': resp.read().decode()}
        except urllib.error.HTTPError as e:
            return {'status_code': e.code, 'body': e.read().decode()}
        except Exception as e:
            return {'status_code': 0, 'body': str(e)}

    # ------------------------------------------------------------------
    # Pre-implemented: Validate tenant access (used by authorizer)
    # ------------------------------------------------------------------
    def validate_tenant_access(self, tenant_id: str) -> bool:
        """Return True if tenant exists and is active."""
        ctx = self.get_tenant_context(tenant_id)
        return bool(ctx and ctx.get('status') == 'active')

    # ------------------------------------------------------------------
    # Pre-implemented: Check tenant limits
    # ------------------------------------------------------------------
    def check_tenant_limits(self, tenant_id: str, operation: str) -> bool:
        """Return True if the tenant's limits allow the given operation."""
        ctx = self.get_tenant_context(tenant_id)
        if not ctx or 'limits' not in ctx:
            return False
        if operation == 'api_call':
            return int(ctx['limits'].get('apiRateLimit', 0)) > 0
        return True


# ---------------------------------------------------------------------------
# Verification script — Task 1.5
# ---------------------------------------------------------------------------

def verify_saas_identity():
    """
    End-to-end verification of the SaaS identity pipeline.
    Demonstrates that the same API returns different tenant context
    based on the SaaS identity in the token.
    """
    username1 = os.environ.get('TEST_USERNAME1', 'testuser1@example.com')
    username2 = os.environ.get('TEST_USERNAME2', 'testuser2@example.com')
    password = os.environ.get('TEST_PASSWORD', 'Workshop@123!')
    api_url = os.environ.get('API_GATEWAY_URL', '')

    if not api_url:
        print("ERROR: API_GATEWAY_URL not set. Run: source /workshop/static/code/module1/setup_env.sh")
        return

    service = SaaSIdentityService()

    print("=== SaaS Identity Verification ===\n")

    # --- Tenant 1 ---
    print(f"--- Tenant 1: {username1} ---")
    tokens1 = service.authenticate_user(username1, password)
    if not tokens1:
        print("ERROR: Authentication failed for tenant 1")
        return
    print("Authenticated: ✓")

    decoded1 = service.decode_id_token(tokens1['IdToken'])
    tenant_id1 = service.extract_tenant_id(decoded1)
    print(f"SaaS Identity (tenant_id): {tenant_id1}")

    context1 = service.get_tenant_context(tenant_id1)
    print(f"Tenant Context: {json.dumps(context1, default=str)}")

    response1 = service.call_protected_api(api_url, tokens1['IdToken'])
    print(f"API Response: {response1['status_code']} — {response1['body']}")

    # --- Tenant 2 ---
    print(f"\n--- Tenant 2: {username2} ---")
    tokens2 = service.authenticate_user(username2, password)
    if not tokens2:
        print("ERROR: Authentication failed for tenant 2")
        return
    print("Authenticated: ✓")

    decoded2 = service.decode_id_token(tokens2['IdToken'])
    tenant_id2 = service.extract_tenant_id(decoded2)
    print(f"SaaS Identity (tenant_id): {tenant_id2}")

    context2 = service.get_tenant_context(tenant_id2)
    print(f"Tenant Context: {json.dumps(context2, default=str)}")

    response2 = service.call_protected_api(api_url, tokens2['IdToken'])
    print(f"API Response: {response2['status_code']} — {response2['body']}")

    # --- Cross-tenant verification ---
    print("\n--- Cross-tenant verification ---")
    if tenant_id1 != tenant_id2 and response1['status_code'] == 200 and response2['status_code'] == 200:
        print("Same API endpoint, different SaaS identity → different tenant context ✓")
    elif response1['status_code'] == 403 or response2['status_code'] == 403:
        print("Got 403 — authorizer may not be deployed yet. Run Task 1.4 (deploy.sh) first.")
    else:
        print("Unexpected result — check the authorizer deployment.")

    # --- Test authorizer decision logic locally ---
    print("\n--- Local authorizer decision test ---")
    auth_result = service.authorize_request(decoded1)
    if auth_result and auth_result.get('status') == 'active':
        print(f"authorize_request(tenant-001): Allow ✓ (tier={auth_result.get('tier')})")
    else:
        print(f"authorize_request(tenant-001): {auth_result}")

    # Test with a fake inactive tenant
    fake_decoded = {'custom:tenant_id': 'tenant-999'}
    auth_result_fake = service.authorize_request(fake_decoded)
    if auth_result_fake is None:
        print("authorize_request(tenant-999): Deny ✓ (fail-closed)")
    else:
        print(f"authorize_request(tenant-999): Unexpected Allow — check Task 1.3")


if __name__ == '__main__':
    verify_saas_identity()
