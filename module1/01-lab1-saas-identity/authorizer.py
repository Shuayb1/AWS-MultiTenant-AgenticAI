# SPDX-License-Identifier: MIT-0
"""
Lambda Authorizer for API Gateway
Validates JWT tokens and enforces SaaS identity by enriching with tenant context.

This authorizer is deployed by deploy.sh which bundles saas_identity.py alongside it.
The SaaSIdentityService provides extract_tenant_id() and get_tenant_context().
"""

import json
import jwt
import os
import sys
from pathlib import Path

# Import SaaSIdentityService from the same directory
sys.path.insert(0, str(Path(__file__).parent))
from saas_identity import SaaSIdentityService


def lambda_handler(event, context):
    """
    API Gateway Lambda Authorizer — the SaaS identity enforcement point.

    Validates JWT token, extracts tenant identity, enriches with context,
    and generates an IAM policy that downstream Lambda functions can read.
    Fail-closed: any error results in Deny.
    """
    print(f"Authorizer event: {json.dumps(event)}")

    # Extract JWT from Authorization header
    token = event['authorizationToken'].replace('Bearer ', '')

    try:
        # Decode JWT (workshop: signature verification disabled)
        decoded = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["RS256", "HS256"],
        )
        print(f"Decoded token: {json.dumps(decoded)}")

        # Use SaaSIdentityService for the decision logic
        identity_service = SaaSIdentityService()
        tenant_context = identity_service.authorize_request(decoded)

        if not tenant_context:
            print("Authorization denied — tenant not found or not active")
            return generate_policy('user', 'Deny', event['methodArn'])

        tenant_id = tenant_context.get('tenantId', '')
        print(f"Tenant {tenant_id} authorized successfully")

        return generate_policy(
            decoded.get('sub', 'user'),
            'Allow',
            event['methodArn'],
            tenant_context,
        )

    except jwt.DecodeError as e:
        print(f"JWT decode error: {str(e)}")
        return generate_policy('user', 'Deny', event['methodArn'])
    except Exception as e:
        print(f"Authorization failed: {str(e)}")
        return generate_policy('user', 'Deny', event['methodArn'])


def generate_policy(principal_id, effect, resource, context=None):
    """
    Generate IAM policy for API Gateway.

    Args:
        principal_id: The principal user identifier
        effect: 'Allow' or 'Deny'
        resource: The resource ARN
        context: Optional tenant context to pass to downstream services
    """
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource,
                }
            ],
        },
    }

    if context:
        policy['context'] = {
            'tenantId': context.get('tenantId', ''),
            'tenantTier': context.get('tier', ''),
            'tenantStatus': context.get('status', ''),
            'tenantContext': json.dumps(context, default=str),
        }

    return policy


# ---------------------------------------------------------------------------
# Local test
# ---------------------------------------------------------------------------

def test_authorizer():
    """Test the authorizer locally."""
    # Create a test JWT
    payload = {
        'sub': 'user-123',
        'custom:tenant_id': 'tenant-001',
        'email': 'testuser1@example.com',
    }
    test_token = jwt.encode(payload, 'secret', algorithm='HS256')

    event = {
        'type': 'TOKEN',
        'authorizationToken': f'Bearer {test_token}',
        'methodArn': 'arn:aws:execute-api:us-east-1:123456789012:abcdef123/prod/GET/resource',
    }

    class MockContext:
        function_name = 'test-authorizer'
        request_id = 'test-request-id'

    result = lambda_handler(event, MockContext())
    print(f"\nAuthorization result:\n{json.dumps(result, indent=2)}")


if __name__ == '__main__':
    test_authorizer()
