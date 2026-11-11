# SPDX-License-Identifier: MIT-0
"""
interceptor.py — Gateway Interceptor: Header Injection + Audit
Module 3, Lab 2

The interceptor is invoked by the Gateway on every MCP request (both target types).

For Lambda targets (query_orders):
  - Injects X-Tenant-ID header so the tool knows which tenant is calling
  - The tool uses this header to scope TVM credentials

For MCP Server targets (query_properties):
  - Does NOT need to inject tenant context (the OAuth token carries it)
  - Still logs the audit trail

For both:
  - Logs a structured audit entry (tenantId, tool, timestamp)

The Gateway has already validated the JWT signature (Cognito authorizer).
Cedar policy has already verified the tenant claim exists.
This interceptor handles the business logic that Cedar can't express.

Env vars: (none — tenant identity comes from JWT claims)
"""

import json
import logging
from datetime import datetime, timezone

import jwt  # PyJWT — signature already verified by Gateway

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _pass_through(request_body: dict) -> dict:
    """Return the request body unchanged (pass-through to target)."""
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": request_body}},
    }


def _error_response(status_code: int, request_id, message: str) -> dict:
    """Return a JSON-RPC error response to the Gateway."""
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": status_code,
                "body": {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": message},
                },
            }
        },
    }


# ---------------------------------------------------------------------------
# Task 2.4a — Extract tenant identity from JWT
# ---------------------------------------------------------------------------

def _extract_tenant_id(auth_header: str) -> str:
    """
    Decode the JWT and return the tenantId.

    The Gateway has already validated the JWT signature — no re-verification needed.
    We only decode to read the custom:tenant_id claim for:
      - Header injection (Lambda targets)
      - Audit logging (all targets)

    Args:
        auth_header: The Authorization header value (e.g., "Bearer eyJ...")

    Returns:
        The tenant ID string.

    Raises:
        ValueError: If the JWT is missing or doesn't contain custom:tenant_id.
    """
    try:
        # TODO: Task 2.4a — Extract tenantId from the JWT claims
        token = auth_header.removeprefix("Bearer ").strip()
        claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["RS256"])
        tenant_id = claims.get("custom:tenant_id", "")
        if not tenant_id:
            raise ValueError("JWT missing custom:tenant_id claim")
        return tenant_id
    except (NotImplementedError, Exception):
        raise


# ---------------------------------------------------------------------------
# Task 2.4b — Handle InvokeTool: inject header (Lambda targets) + audit (all)
# ---------------------------------------------------------------------------

def _handle_invoke_tool(event: dict, tenant_id: str) -> dict:
    """
    Handle a tools/call request:
      1. Log an audit trail entry (tenant, tool, timestamp)
      2. Propagate x-tenant-id header (Lambda targets read from context.client_context)
      3. Propagate x-user-token header (MCP Server validates OAuth + extracts tenant from user token)

    Does NOT override the Authorization header — lets the OAuth token from the
    credential provider pass through to establish Gateway-to-MCP-Server trust.

    Args:
        event: The Gateway interceptor event.
        tenant_id: The resolved tenant ID.

    Returns:
        Interceptor response with headers and request body.
    """
    try:
        # TODO: Task 2.4b — Propagate headers and log the invocation
        mcp_payload = event.get("mcp", {})
        gateway_request = mcp_payload.get("gatewayRequest", {})
        request_body = gateway_request.get("body", {})
        headers = gateway_request.get("headers", {})
        auth_header = headers.get("Authorization", headers.get("authorization", ""))
        tool_name = request_body.get("params", {}).get("name", "") if isinstance(request_body, dict) else ""

        # Audit log — every tool invocation attributed to a tenant
        logger.info(json.dumps({
            "event": "tool_invocation",
            "tenant_id": tenant_id,
            "tool": tool_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

        # Propagate headers (does NOT override Authorization — OAuth token passes through)
        # - x-tenant-id: Lambda targets read from context.client_context
        # - x-user-token: MCP Server extracts tenant_id from user JWT claims
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayRequest": {
                    "headers": {
                        "x-tenant-id": tenant_id,
                        "x-user-token": auth_header.removeprefix("Bearer ").strip(),
                    },
                    "body": request_body,
                }
            }
        }
    except (NotImplementedError, Exception) as e:
        logger.error("_handle_invoke_tool failed: %s", e)
        request_body = event.get("mcp", {}).get("gatewayRequest", {}).get("body", {})
        return _error_response(500, request_body.get("id", 1), str(e))


# ---------------------------------------------------------------------------
# Lambda handler — invoked by AgentCore Gateway on every MCP request
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:
    """
    AgentCore Gateway interceptor entry point.

    Called after:
      1. Cognito JWT validation (signature verified)
      2. Cedar policy evaluation (tenant claim verified)

    This function:
      - Extracts tenantId from JWT
      - On InvokeTool: injects X-Tenant-ID header + logs audit trail
      - On ListTools: passes through (tools visible to all tenants by default)
    """
    try:
        # Get the MCP request body from the interceptor input payload
        mcp_payload = event.get("mcp", {})
        gateway_request = mcp_payload.get("gatewayRequest", {})
        request_body = gateway_request.get("body", {})
        mcp_method = request_body.get("method", "") if isinstance(request_body, dict) else ""

        # For non-InvokeTool requests (initialize, tools/list), pass through unchanged
        if mcp_method != "tools/call":
            return _pass_through(request_body)

        # Extract auth header for InvokeTool
        headers = gateway_request.get("headers", {})
        auth_header = headers.get("Authorization", headers.get("authorization", ""))

        tenant_id = _extract_tenant_id(auth_header)
        logger.info("tenant=%s method=%s", tenant_id, mcp_method)

        return _handle_invoke_tool(event, tenant_id)
    except (NotImplementedError, Exception) as e:
        logger.error("lambda_handler error: %s", e)
        return _error_response(500, 1, str(e))
