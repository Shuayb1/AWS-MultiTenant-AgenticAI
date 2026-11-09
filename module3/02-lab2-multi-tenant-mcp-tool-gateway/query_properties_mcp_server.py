# SPDX-License-Identifier: MIT-0
"""
query_properties_mcp_server.py — Self-hosted MCP Server: OAuth-protected Aurora properties
Module 3, Lab 2

Simulates a 3rd party MCP Server with its own OAuth-based authentication.
Deployed as an ECS Fargate service behind an ALB and registered as an
MCP Server target on the AgentCore Gateway.

Authentication flow:
  1. Gateway fetches OAuth token from AgentCore Identity (credential provider)
  2. Gateway calls this MCP Server with: Authorization: Bearer <oauth_token>
  3. This server validates the OAuth token against Cognito JWKS (establishes Gateway trust)
  4. Interceptor propagates user JWT as x-user-token header
  5. This server decodes x-user-token to extract custom:tenant_id (tenant identity)
  6. Sets RLS context on Aurora PostgreSQL
  7. Queries — RLS filters to current tenant's rows only

Two-layer auth:
  - Authorization header (OAuth token): Proves the caller is the trusted Gateway
  - x-user-token header (user JWT): Carries tenant identity for data scoping

Env vars:
  CLUSTER_ARN       — Aurora cluster ARN
  SECRET_ARN        — Secrets Manager ARN for DB credentials
  DB_NAME           — Database name (default: saasdb)
  COGNITO_POOL_ID   — Cognito User Pool ID (for JWKS URL)
  EXPECTED_AUDIENCE — Expected audience claim in the token
  AWS_REGION_NAME   — AWS region (default: us-east-1)
  PORT              — HTTP port to listen on (default: 8080)
"""

import json
import os

import boto3
import jwt
from jwt import PyJWKClient
from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "")
SECRET_ARN = os.environ.get("SECRET_ARN", "")
DB_NAME = os.environ.get("DB_NAME", "saasdb")
REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
COGNITO_POOL_ID = os.environ.get("COGNITO_POOL_ID", "")
EXPECTED_AUDIENCE = os.environ.get("EXPECTED_AUDIENCE", "")
PORT = int(os.environ.get("PORT", "8080"))

# JWKS client for token validation (cached — fetches keys once)
COGNITO_JWKS_URL = f"https://cognito-idp.{REGION}.amazonaws.com/{COGNITO_POOL_ID}/.well-known/jwks.json"
jwks_client = PyJWKClient(COGNITO_JWKS_URL) if COGNITO_POOL_ID else None

rds = boto3.client("rds-data", region_name=REGION)
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Health check (used by ALB target group)
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


# ---------------------------------------------------------------------------
# MCP Server protocol handler
# ---------------------------------------------------------------------------

@app.route("/mcp", methods=["POST", "GET"])
def mcp_handler():
    """
    MCP Server endpoint implementing the JSON-RPC protocol.

    Handles:
      - GET  → SSE endpoint (returns 200 with empty event stream for protocol compatibility)
      - POST → JSON-RPC (initialize, tools/list, tools/call)
    """
    # GET /mcp is used by MCP protocol 2025-06-18 for SSE streaming
    if request.method == "GET":
        return "", 200

    body = request.get_json(force=True)
    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id", 1)

    try:
        print(f"[MCP] method={method} headers={dict(request.headers)}")

        if method == "initialize":
            return _mcp_response(request_id, {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "query-properties-mcp-server", "version": "1.0.0"},
            })

        if method == "tools/list":
            return _mcp_response(request_id, {
                "tools": [{
                    "name": "query_properties",
                    "description": "Query properties managed by the current tenant",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer", "default": 10}},
                    },
                }]
            })

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            print(f"[MCP] tools/call tool={tool_name} arguments={arguments}")

            if tool_name == "query_properties":
                # Step 1: Validate OAuth token (Authorization header) — establishes Gateway trust
                auth_header = request.headers.get("Authorization", "")
                oauth_token = auth_header.removeprefix("Bearer ").strip()
                if oauth_token and jwks_client:
                    try:
                        signing_key = jwks_client.get_signing_key_from_jwt(oauth_token)
                        jwt.decode(oauth_token, signing_key.key, algorithms=["RS256"], options={"verify_aud": False})
                        print(f"[MCP] OAuth token validated ✓ (Gateway trust established)")
                    except Exception as e:
                        print(f"[MCP] OAuth token validation failed: {e}")
                        return _mcp_error(request_id, -32600, f"Unauthorized: OAuth token invalid - {e}")

                # Step 2: Extract tenant_id from x-user-token header (user JWT forwarded by interceptor)
                user_token = request.headers.get("x-user-token", "")
                tenant_id = ""
                if user_token:
                    try:
                        # Decode without signature verification — interceptor already validated
                        claims = jwt.decode(user_token, options={"verify_signature": False}, algorithms=["RS256"])
                        tenant_id = claims.get("custom:tenant_id", "")
                        print(f"[MCP] tenant_id from x-user-token: '{tenant_id}'")
                    except Exception as e:
                        print(f"[MCP] x-user-token decode failed: {e}")

                # Fallback: try JWT from Authorization header (direct invocation)
                if not tenant_id:
                    auth_header = request.headers.get("Authorization", "")
                    token = auth_header.removeprefix("Bearer ").strip()
                    print(f"[MCP] Fallback: auth_header present={bool(token)}")
                    if token:
                        try:
                            signing_key = jwks_client.get_signing_key_from_jwt(token)
                            claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=EXPECTED_AUDIENCE)
                            tenant_id = claims.get("custom:tenant_id", "")
                            print(f"[MCP] tenant_id from JWT: '{tenant_id}'")
                        except Exception as e:
                            print(f"[MCP] JWT validation failed: {e}")

                if not tenant_id:
                    print("[MCP] ERROR: tenant_id missing from both arguments and JWT")
                    return _mcp_error(request_id, -32600, "tenant_id missing - provide in arguments or Authorization header")

                print(f"[MCP] Calling _query_properties(tenant_id={tenant_id})")
                result = _query_properties(tenant_id, arguments)
                if "error" in result:
                    return _mcp_error(request_id, -32600, result["error"])

                return _mcp_response(request_id, {
                    "content": [{"type": "text", "text": json.dumps(result, default=str)}]
                })

        return _mcp_error(request_id, -32601, f"Method not found: {method}")
    except Exception as e:
        import traceback
        print(f"[MCP] ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return _mcp_error(request_id, -32603, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Task 2.2 — Query properties with OAuth token validation + RLS
# ---------------------------------------------------------------------------

def _query_properties(tenant_id: str, arguments: dict) -> dict:
    """
    Set RLS context on Aurora and query properties for the given tenant.

    The tenant_id has already been validated by:
      - The interceptor (extracted from JWT claims)
      - Or direct JWT validation (if called without the Gateway)

    Args:
        tenant_id: Validated tenant identifier.
        arguments: Tool arguments (e.g., {"limit": 10}).

    Returns:
        Dict with tenant_id and properties, or error.
    """
    limit = arguments.get("limit", 10)

    try:
        # TODO: Task 2.2 — Set RLS context and query Aurora for tenant properties
        raise NotImplementedError("Complete Task 2.2 — query_properties")  # REPLACE THIS LINE
    except Exception as e:
        import traceback
        print(f"ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return {"error": str(e), "type": type(e).__name__}


# ---------------------------------------------------------------------------
# MCP protocol helpers
# ---------------------------------------------------------------------------

def _mcp_response(request_id, result: dict):
    return jsonify({"jsonrpc": "2.0", "id": request_id, "result": result}), 200


def _mcp_error(request_id, code: int, message: str):
    return jsonify({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}), 200


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"MCP Server listening on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)
