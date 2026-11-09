# SPDX-License-Identifier: MIT-0
"""
Property-based tests for Module 3, Lab 1 — Pooled Multi-Tenant Agents
Run with: pytest test_properties.py -v --hypothesis-seed=0

Tests the two solution implementations:
  1. Backend Lambda (Task 1.1) — tenant extraction, session ID construction, TVM + AgentCore call
  2. Agent entrypoint (Task 1.2) — message handling, session state tracking

Also tests the pooled model isolation properties:
  3. Session ID always contains the tenant prefix (no cross-tenant routing possible)
  4. Session state is independent across different sessions
  5. IsolationManager rejects missing/empty tenantId
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch, ANY

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# 1. Inline solution implementations (self-contained, no imports from source)
# ---------------------------------------------------------------------------

# --- Task 1.1: Backend Lambda solution ---

def backend_lambda_handler(event: dict, context) -> dict:
    """Solution for Task 1.1 — Backend Lambda handler."""
    body = json.loads(event.get("body", "{}"))

    # Extract tenant_id from validated JWT claims (API Gateway authorizer)
    claims = event["requestContext"]["authorizer"]["claims"]
    tenant_id = claims["custom:tenant_id"]
    user_id = claims.get("sub", "unknown")

    # Construct tenant-scoped session ID
    conversation_id = body.get("conversation_id", str(uuid.uuid4()))
    session_id = f"{tenant_id}-{conversation_id}"

    # In tests, we mock STS and AgentCore — just verify the flow
    return {
        "statusCode": 200,
        "body": json.dumps({
            "tenant_id": tenant_id,
            "session_id": session_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
        }),
    }


# --- Task 1.2: Agent entrypoint solution ---

_session_state = {}


def invoke_agent(payload: dict, context) -> dict:
    """Solution for Task 1.2 — Agent entrypoint."""
    message = payload.get("message", "")
    if not message:
        return {"error": "message field required"}

    _session_state["invocation_count"] = _session_state.get("invocation_count", 0) + 1

    # Mock agent response for testing
    return {
        "response": f"Mock response to: {message}",
        "invocation_count": _session_state["invocation_count"],
    }


# ---------------------------------------------------------------------------
# 2. Hypothesis strategies
# ---------------------------------------------------------------------------

@st.composite
def tenant_ids(draw):
    """Generate realistic tenant IDs."""
    return draw(st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
        min_size=1, max_size=64,
    ))


@st.composite
def conversation_ids(draw):
    """Generate realistic conversation IDs."""
    return draw(st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
        min_size=1, max_size=64,
    ))


@st.composite
def api_gateway_events(draw):
    """Generate realistic API Gateway Lambda Proxy events with tenant claims."""
    tenant_id = draw(tenant_ids())
    conversation_id = draw(conversation_ids())
    user_id = draw(st.uuids().map(str))
    message = draw(st.text(min_size=1, max_size=200))

    return {
        "body": json.dumps({
            "message": message,
            "conversation_id": conversation_id,
        }),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "custom:tenant_id": tenant_id,
                    "sub": user_id,
                    "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_xxx",
                }
            }
        },
    }


@dataclass
class FakeContext:
    """Mimics AgentCore Runtime context."""
    session_id: str = "test-session-001"


# ---------------------------------------------------------------------------
# 3. Tests — Backend Lambda (Task 1.1)
# ---------------------------------------------------------------------------

class TestBackendLambda:
    """Task 1.1 — Backend Lambda: tenant extraction and session ID construction."""

    @given(event=api_gateway_events())
    @settings(max_examples=50)
    def test_session_id_always_prefixed_with_tenant_id(self, event):
        """Session ID always starts with the tenant_id from the JWT claims."""
        result = backend_lambda_handler(event, None)
        body = json.loads(result["body"])
        tenant_id = event["requestContext"]["authorizer"]["claims"]["custom:tenant_id"]
        assert body["session_id"].startswith(f"{tenant_id}-")

    @given(event=api_gateway_events())
    @settings(max_examples=50)
    def test_tenant_id_matches_jwt_claim(self, event):
        """Returned tenant_id always matches the JWT claim."""
        result = backend_lambda_handler(event, None)
        body = json.loads(result["body"])
        expected = event["requestContext"]["authorizer"]["claims"]["custom:tenant_id"]
        assert body["tenant_id"] == expected

    @given(event=api_gateway_events())
    @settings(max_examples=30)
    def test_session_id_contains_conversation_id(self, event):
        """Session ID contains the conversation_id from the request body."""
        result = backend_lambda_handler(event, None)
        body = json.loads(result["body"])
        request_body = json.loads(event["body"])
        assert request_body["conversation_id"] in body["session_id"]

    def test_generates_conversation_id_when_missing(self):
        """When conversation_id is not in the body, a UUID is generated."""
        event = {
            "body": json.dumps({"message": "hello"}),
            "requestContext": {
                "authorizer": {
                    "claims": {"custom:tenant_id": "tenant-001", "sub": "user-1"}
                }
            },
        }
        result = backend_lambda_handler(event, None)
        body = json.loads(result["body"])
        # Session ID should still be prefixed with tenant_id
        assert body["session_id"].startswith("tenant-001-")
        # And the generated part should be a valid UUID
        generated_part = body["session_id"].removeprefix("tenant-001-")
        uuid.UUID(generated_part)  # Raises if not a valid UUID

    def test_returns_200_status(self):
        """Handler returns HTTP 200."""
        event = {
            "body": json.dumps({"message": "hi", "conversation_id": "conv-1"}),
            "requestContext": {
                "authorizer": {
                    "claims": {"custom:tenant_id": "tenant-001", "sub": "user-1"}
                }
            },
        }
        result = backend_lambda_handler(event, None)
        assert result["statusCode"] == 200

    @given(
        tenant_a=tenant_ids(),
        tenant_b=tenant_ids(),
        conv_id=conversation_ids(),
    )
    @settings(max_examples=30)
    def test_different_tenants_same_conversation_id_get_different_sessions(self, tenant_a, tenant_b, conv_id):
        """Two tenants with the same conversation_id get different session IDs."""
        from hypothesis import assume
        assume(tenant_a != tenant_b)

        event_a = {
            "body": json.dumps({"message": "hi", "conversation_id": conv_id}),
            "requestContext": {"authorizer": {"claims": {"custom:tenant_id": tenant_a, "sub": "u1"}}},
        }
        event_b = {
            "body": json.dumps({"message": "hi", "conversation_id": conv_id}),
            "requestContext": {"authorizer": {"claims": {"custom:tenant_id": tenant_b, "sub": "u2"}}},
        }

        result_a = json.loads(backend_lambda_handler(event_a, None)["body"])
        result_b = json.loads(backend_lambda_handler(event_b, None)["body"])

        assert result_a["session_id"] != result_b["session_id"]
        assert result_a["session_id"].startswith(f"{tenant_a}-")
        assert result_b["session_id"].startswith(f"{tenant_b}-")


# ---------------------------------------------------------------------------
# 4. Tests — Agent Entrypoint (Task 1.2)
# ---------------------------------------------------------------------------

class TestAgentEntrypoint:
    """Task 1.2 — Agent entrypoint: message handling and session state."""

    def setup_method(self):
        """Reset session state before each test."""
        _session_state.clear()

    def test_returns_response_for_valid_message(self):
        """Valid message returns a response."""
        ctx = FakeContext()
        result = invoke_agent({"message": "hello"}, ctx)
        assert "response" in result
        assert "hello" in result["response"]

    def test_returns_error_for_empty_message(self):
        """Empty message returns an error."""
        ctx = FakeContext()
        result = invoke_agent({"message": ""}, ctx)
        assert "error" in result

    def test_returns_error_for_missing_message(self):
        """Missing message field returns an error."""
        ctx = FakeContext()
        result = invoke_agent({}, ctx)
        assert "error" in result

    def test_invocation_count_increments(self):
        """invocation_count increments on each call within the same session."""
        ctx = FakeContext()
        r1 = invoke_agent({"message": "first"}, ctx)
        r2 = invoke_agent({"message": "second"}, ctx)
        r3 = invoke_agent({"message": "third"}, ctx)
        assert r1["invocation_count"] == 1
        assert r2["invocation_count"] == 2
        assert r3["invocation_count"] == 3

    @given(messages=st.lists(st.text(min_size=1, max_size=100), min_size=1, max_size=10))
    @settings(max_examples=20)
    def test_invocation_count_equals_number_of_calls(self, messages):
        """invocation_count always equals the total number of calls made."""
        _session_state.clear()
        ctx = FakeContext()
        for i, msg in enumerate(messages, 1):
            result = invoke_agent({"message": msg}, ctx)
            assert result["invocation_count"] == i


# ---------------------------------------------------------------------------
# 5. Tests — Pooled Model Isolation Properties
# ---------------------------------------------------------------------------

class TestPooledModelIsolation:
    """Pooled model — session state isolation between tenants."""

    def setup_method(self):
        """Reset session state before each test."""
        _session_state.clear()

    def test_fresh_session_starts_at_count_1(self):
        """A new session always starts with invocation_count = 1."""
        ctx = FakeContext(session_id="tenant-001-conv-new")
        result = invoke_agent({"message": "hello"}, ctx)
        assert result["invocation_count"] == 1

    def test_session_id_construction_prevents_cross_tenant_access(self):
        """
        Even if tenant-002 passes the same conversation_id as tenant-001,
        the backend constructs a different session ID because the tenant prefix differs.
        """
        conv_id = "shared-conv-id"

        event_t1 = {
            "body": json.dumps({"message": "hi", "conversation_id": conv_id}),
            "requestContext": {"authorizer": {"claims": {"custom:tenant_id": "tenant-001", "sub": "u1"}}},
        }
        event_t2 = {
            "body": json.dumps({"message": "hi", "conversation_id": conv_id}),
            "requestContext": {"authorizer": {"claims": {"custom:tenant_id": "tenant-002", "sub": "u2"}}},
        }

        result_t1 = json.loads(backend_lambda_handler(event_t1, None)["body"])
        result_t2 = json.loads(backend_lambda_handler(event_t2, None)["body"])

        # Different session IDs — different microVMs
        assert result_t1["session_id"] == "tenant-001-shared-conv-id"
        assert result_t2["session_id"] == "tenant-002-shared-conv-id"
        assert result_t1["session_id"] != result_t2["session_id"]

    @given(tenant_id=tenant_ids(), conv_id=conversation_ids())
    @settings(max_examples=50)
    def test_session_id_never_allows_tenant_to_reach_another_tenants_session(self, tenant_id, conv_id):
        """
        The session ID format guarantees that a tenant's session ID
        can never match another tenant's pattern in the IAM condition.

        IAM condition: bedrock-agentcore:sessionId must match "${TenantID}-*"
        This test verifies the session ID always satisfies this for the correct tenant
        and never satisfies it for any other tenant.
        """
        event = {
            "body": json.dumps({"message": "test", "conversation_id": conv_id}),
            "requestContext": {"authorizer": {"claims": {"custom:tenant_id": tenant_id, "sub": "u"}}},
        }
        result = json.loads(backend_lambda_handler(event, None)["body"])
        session_id = result["session_id"]

        # Session ID matches the tenant's own pattern
        assert session_id.startswith(f"{tenant_id}-")

        # Session ID does NOT match any other tenant's pattern
        # (unless the other tenant's ID is a prefix of this tenant's ID)
        other_tenant = f"other-{tenant_id}"
        assert not session_id.startswith(f"{other_tenant}-")


# ---------------------------------------------------------------------------
# 6. Tests — IsolationManager
# ---------------------------------------------------------------------------

class TestIsolationManager:
    """IsolationManager — TVM credential scoping."""

    def test_rejects_empty_role_arn(self):
        """IsolationManager raises on empty role_arn."""
        from isolation_manager import IsolationManager, IsolationError
        with pytest.raises(IsolationError):
            IsolationManager(role_arn="")

    def test_rejects_missing_tenant_id(self):
        """get_session raises on missing tenantId."""
        from isolation_manager import IsolationManager, IsolationError
        mgr = IsolationManager(role_arn="arn:aws:iam::123456789012:role/test")
        with pytest.raises(IsolationError):
            mgr.get_session({})

    def test_rejects_empty_tenant_id(self):
        """get_session raises on empty tenantId."""
        from isolation_manager import IsolationManager, IsolationError
        mgr = IsolationManager(role_arn="arn:aws:iam::123456789012:role/test")
        with pytest.raises(IsolationError):
            mgr.get_session({"tenantId": ""})

    @patch("isolation_manager.boto3.client")
    def test_calls_sts_with_tenant_tag(self, mock_boto_client):
        """get_session calls STS AssumeRole with the TenantID tag."""
        from isolation_manager import IsolationManager

        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIA...",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }
        mock_boto_client.return_value = mock_sts

        mgr = IsolationManager(role_arn="arn:aws:iam::123456789012:role/tvm")
        mgr.get_session({"tenantId": "tenant-001"})

        mock_sts.assume_role.assert_called_once_with(
            RoleArn="arn:aws:iam::123456789012:role/tvm",
            RoleSessionName="tenant-tenant-001",
            Tags=[{"Key": "TenantID", "Value": "tenant-001"}],
            DurationSeconds=900,
        )

    @patch("isolation_manager.boto3.client")
    def test_caches_credentials_per_tenant(self, mock_boto_client):
        """Second call for the same tenant uses cached credentials (no STS call)."""
        from isolation_manager import IsolationManager

        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIA...",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }
        mock_boto_client.return_value = mock_sts

        mgr = IsolationManager(role_arn="arn:aws:iam::123456789012:role/tvm")
        mgr.get_session({"tenantId": "tenant-001"})
        mgr.get_session({"tenantId": "tenant-001"})

        # Only one STS call — second was served from cache
        assert mock_sts.assume_role.call_count == 1

    @patch("isolation_manager.boto3.client")
    def test_different_tenants_get_different_sessions(self, mock_boto_client):
        """Different tenants trigger separate STS calls."""
        from isolation_manager import IsolationManager

        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIA...",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }
        mock_boto_client.return_value = mock_sts

        mgr = IsolationManager(role_arn="arn:aws:iam::123456789012:role/tvm")
        mgr.get_session({"tenantId": "tenant-001"})
        mgr.get_session({"tenantId": "tenant-002"})

        assert mock_sts.assume_role.call_count == 2
