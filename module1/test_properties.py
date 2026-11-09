# SPDX-License-Identifier: MIT-0
"""
Property-based tests for Module 1: SaaS Foundations

These tests validate the correctness invariants of the tenant identity and
context management logic that participants implement in the lab exercises.

They use:
  - hypothesis  — property-based test engine
  - moto        — AWS service mocking (no live AWS calls required)
  - pytest      — test runner

Run with:
    pytest test_properties.py -v --hypothesis-seed=0

Feature: workshop-content-enhancement
"""

import json
import os
import sys
import boto3
import pytest
import jwt

from datetime import datetime
from typing import Dict, List, Optional
from unittest.mock import patch

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from hypothesis.strategies import composite

# ---------------------------------------------------------------------------
# Inline solution implementations
# These mirror the solution snippets participants copy from the lab content.
# They are defined here so the property tests are self-contained and do not
# depend on participants having completed the TODO stubs.
# ---------------------------------------------------------------------------

# --- JWT extraction logic (solution for authorizer.py Tasks 3.1 / cognito_auth_flow.py Task 1.3) ---

def _extract_tenant_id_from_token(token: str) -> Optional[str]:
    """Extract tenant_id from a JWT token string (Bearer prefix optional)."""
    raw = token.replace('Bearer ', '')
    decoded = jwt.decode(raw, options={"verify_signature": False}, algorithms=["HS256", "RS256"])
    return decoded.get('custom:tenant_id')


def _encode_token(tenant_id: str, user_id: str) -> str:
    """Encode a JWT with custom:tenant_id claim (HS256, no real secret needed for tests)."""
    payload = {
        'sub': user_id,
        'custom:tenant_id': tenant_id,
        'email': 'user@example.com',
    }
    return jwt.encode(payload, 'test-secret', algorithm='HS256')


# --- TenantContextService solution (Tasks 2.1 – 2.4) ---

class _TenantContextServiceSolution:
    """
    Solution implementation of TenantContextService.
    Used by property tests so they run against correct logic regardless of
    whether participants have filled in the TODO stubs.
    """

    def __init__(self, table_name: str = 'TenantMetadata'):
        self.dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        self.table = self.dynamodb.Table(table_name)

    def get_tenant_context(self, tenant_id: str) -> Optional[Dict]:
        try:
            response = self.table.get_item(Key={'tenantId': tenant_id})
            return response.get('Item')
        except Exception as e:
            print(f"Error retrieving tenant context: {e}")
            return None

    def validate_tenant_access(self, tenant_id: str, resource: str) -> bool:
        tenant_context = self.get_tenant_context(tenant_id)
        if not tenant_context:
            return False
        if tenant_context.get('status') != 'active':
            return False
        return True

    def check_tenant_limits(self, tenant_id: str, operation: str) -> bool:
        tenant_context = self.get_tenant_context(tenant_id)
        if not tenant_context or 'limits' not in tenant_context:
            return False
        limits = tenant_context['limits']
        if operation == 'api_call':
            api_limit = limits.get('apiRateLimit', 0)
            if api_limit <= 0:
                return False
        return True

    def create_tenant(self, tenant_data: Dict) -> bool:
        try:
            data = dict(tenant_data)
            if 'createdAt' not in data:
                data['createdAt'] = datetime.utcnow().isoformat() + 'Z'
            if 'status' not in data:
                data['status'] = 'active'
            self.table.put_item(Item=data)
            return True
        except Exception as e:
            print(f"Error creating tenant: {e}")
            return False


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

VALID_TIERS = ['free', 'standard', 'premium']
VALID_STATUSES = ['active', 'inactive', 'suspended', 'trial']
VALID_FEATURES = ['advanced-analytics', 'api-access', 'reporting', 'export', 'webhooks']


@composite
def tenant_dicts(draw) -> Dict:
    """Generate a valid tenant metadata dict."""
    tenant_id = draw(st.text(
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-'),
        min_size=1, max_size=40,
    ))
    tenant_name = draw(st.text(min_size=1, max_size=80))
    tier = draw(st.sampled_from(VALID_TIERS))
    status = draw(st.sampled_from(VALID_STATUSES))
    features = draw(st.lists(st.sampled_from(VALID_FEATURES), min_size=0, max_size=5, unique=True))
    api_rate_limit = draw(st.integers(min_value=0, max_value=10000))
    max_users = draw(st.integers(min_value=1, max_value=10000))

    return {
        'tenantId': tenant_id,
        'tenantName': tenant_name,
        'tier': tier,
        'status': status,
        'features': features,
        'limits': {
            'maxUsers': max_users,
            'apiRateLimit': api_rate_limit,
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dynamodb_table(monkeypatch):
    """
    Create a mocked DynamoDB TenantMetadata table using moto.
    Yields the boto3 Table resource.
    """
    import moto
    with moto.mock_aws():
        os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
        # Provide dummy credentials so moto doesn't complain
        monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'testing')
        monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
        monkeypatch.setenv('AWS_SECURITY_TOKEN', 'testing')
        monkeypatch.setenv('AWS_SESSION_TOKEN', 'testing')

        ddb = boto3.resource('dynamodb', region_name='us-east-1')
        table = ddb.create_table(
            TableName='TenantMetadata',
            KeySchema=[{'AttributeName': 'tenantId', 'KeyType': 'HASH'}],
            AttributeDefinitions=[{'AttributeName': 'tenantId', 'AttributeType': 'S'}],
            BillingMode='PAY_PER_REQUEST',
        )
        table.wait_until_exists()
        yield table


# ---------------------------------------------------------------------------
# Property 1: JWT tenant_id extraction round-trip
# Feature: workshop-content-enhancement, Property 1: JWT tenant_id extraction round-trip
# ---------------------------------------------------------------------------

@given(
    tenant_id=st.text(min_size=1, max_size=64),
    user_id=st.text(min_size=1, max_size=64),
)
@settings(max_examples=200)
def test_property1_jwt_tenant_id_round_trip(tenant_id: str, user_id: str):
    """
    Property 1: JWT tenant_id extraction round-trip

    For any non-empty tenant_id and user_id:
      encode(tenant_id, user_id) → token
      extract_tenant_id(token)   → tenant_id  (exact match)

    Validates: Requirements 1.1, 5.4
    """
    token = _encode_token(tenant_id, user_id)
    extracted = _extract_tenant_id_from_token(token)
    assert extracted == tenant_id, (
        f"Expected tenant_id={tenant_id!r} but got {extracted!r}"
    )


@given(
    tenant_id=st.text(min_size=1, max_size=64),
    user_id=st.text(min_size=1, max_size=64),
)
@settings(max_examples=100)
def test_property1_bearer_prefix_stripped(tenant_id: str, user_id: str):
    """
    Property 1 (variant): Bearer prefix is stripped before decoding.

    The authorizer receives 'Bearer <token>' from API Gateway.
    Extraction must work identically whether or not the prefix is present.
    """
    token = _encode_token(tenant_id, user_id)
    assert _extract_tenant_id_from_token(f'Bearer {token}') == tenant_id
    assert _extract_tenant_id_from_token(token) == tenant_id


# ---------------------------------------------------------------------------
# Property 2: Tenant context DynamoDB round-trip
# Feature: workshop-content-enhancement, Property 2: Tenant context DynamoDB round-trip
# ---------------------------------------------------------------------------

@given(tenant=tenant_dicts())
@settings(max_examples=100)
def test_property2_dynamodb_round_trip(tenant: Dict, dynamodb_table, monkeypatch):
    """
    Property 2: Tenant context DynamoDB round-trip

    For any valid tenant dict:
      create_tenant(tenant) → True
      get_tenant_context(tenant['tenantId']) → dict with all original fields intact

    Validates: Requirements 1.1, 1.2
    """
    import moto
    with moto.mock_aws():
        monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'testing')
        monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
        monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')

        ddb = boto3.resource('dynamodb', region_name='us-east-1')
        # Re-create table inside this mock context
        try:
            table = ddb.create_table(
                TableName='TenantMetadata',
                KeySchema=[{'AttributeName': 'tenantId', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'tenantId', 'AttributeType': 'S'}],
                BillingMode='PAY_PER_REQUEST',
            )
            table.wait_until_exists()
        except Exception:
            pass

        service = _TenantContextServiceSolution()
        result = service.create_tenant(tenant)
        assert result is True, "create_tenant should return True on success"

        retrieved = service.get_tenant_context(tenant['tenantId'])
        assert retrieved is not None, (
            f"get_tenant_context returned None for tenantId={tenant['tenantId']!r}"
        )

        # All five core fields must survive the round-trip unchanged
        for field in ('tenantId', 'tenantName', 'tier', 'status', 'features'):
            assert retrieved[field] == tenant[field], (
                f"Field '{field}' mismatch: stored={tenant[field]!r}, retrieved={retrieved[field]!r}"
            )

        # limits sub-map must also be intact
        assert retrieved['limits']['apiRateLimit'] == tenant['limits']['apiRateLimit']
        assert retrieved['limits']['maxUsers'] == tenant['limits']['maxUsers']


# ---------------------------------------------------------------------------
# Property 3: Active-only authorization
# Feature: workshop-content-enhancement, Property 3: Active-only authorization
# ---------------------------------------------------------------------------

@given(
    tenant=tenant_dicts(),
    resource=st.sampled_from(VALID_FEATURES + ['unknown-resource']),
)
@settings(max_examples=200)
def test_property3_active_only_authorization(tenant: Dict, resource: str, monkeypatch):
    """
    Property 3: Active-only authorization

    validate_tenant_access returns True if and only if:
      - tenant exists in DynamoDB, AND
      - tenant['status'] == 'active'

    The resource argument does not affect the result in the current
    implementation (access is gated on status only, not feature list).

    Validates: Requirements 1.2, 5.4
    """
    import moto
    with moto.mock_aws():
        monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'testing')
        monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
        monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')

        ddb = boto3.resource('dynamodb', region_name='us-east-1')
        try:
            table = ddb.create_table(
                TableName='TenantMetadata',
                KeySchema=[{'AttributeName': 'tenantId', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'tenantId', 'AttributeType': 'S'}],
                BillingMode='PAY_PER_REQUEST',
            )
            table.wait_until_exists()
        except Exception:
            pass

        service = _TenantContextServiceSolution()
        service.create_tenant(tenant)

        result = service.validate_tenant_access(tenant['tenantId'], resource)
        expected = (tenant['status'] == 'active')

        assert result == expected, (
            f"validate_tenant_access returned {result} for status={tenant['status']!r}, "
            f"resource={resource!r}; expected {expected}"
        )


@given(resource=st.sampled_from(VALID_FEATURES))
@settings(max_examples=50)
def test_property3_nonexistent_tenant_always_denied(resource: str, monkeypatch):
    """
    Property 3 (variant): A tenant that was never created is always denied.

    validate_tenant_access must return False for any tenant_id not in DynamoDB.
    """
    import moto
    with moto.mock_aws():
        monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'testing')
        monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
        monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')

        ddb = boto3.resource('dynamodb', region_name='us-east-1')
        try:
            ddb.create_table(
                TableName='TenantMetadata',
                KeySchema=[{'AttributeName': 'tenantId', 'KeyType': 'HASH'}],
                AttributeDefinitions=[{'AttributeName': 'tenantId', 'AttributeType': 'S'}],
                BillingMode='PAY_PER_REQUEST',
            ).wait_until_exists()
        except Exception:
            pass

        service = _TenantContextServiceSolution()
        result = service.validate_tenant_access('tenant-does-not-exist', resource)
        assert result is False, (
            "validate_tenant_access must return False for a non-existent tenant"
        )
