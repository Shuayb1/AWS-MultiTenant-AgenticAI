# SPDX-License-Identifier: MIT-0
"""
agent.py — Pooled Agent with MCP Tool Gateway (HTTP client)
Module 3, Lab 2

Connects to the AgentCore Gateway via HTTP JSON-RPC (MCP protocol over HTTP).
No MCP client library needed — uses plain requests to call the Gateway.

The Gateway handles:
  - JWT validation (Cognito)
  - Cedar policy (hard boundary)
  - Interceptor (tenant_id injection for Lambda targets, audit for all)
  - Credential provider (OAuth token for MCP Server targets)

The agent discovers tools via tools/list and invokes them via tools/call.
It doesn't know about tenancy — isolation is enforced by the Gateway.

Env vars:
  GATEWAY_ENDPOINT — AgentCore Gateway MCP endpoint URL (https://...gateway.../mcp)
  AWS_REGION       — AWS region (default: us-east-1)
"""

import json
import os

import requests
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
GATEWAY_ENDPOINT = os.environ.get("GATEWAY_ENDPOINT", "")

SYSTEM_PROMPT = """You are a helpful assistant for a multi-tenant SaaS platform.
You have access to tools via the MCP Gateway. Use them to answer user questions.
Only use tools that are available to you.
Do not include thinking tags, internal reasoning, or XML tags in your response. Respond directly and concisely."""

app = BedrockAgentCoreApp()


@app.ping
def health_check():
    from bedrock_agentcore.runtime import PingStatus
    return PingStatus.HEALTHY


# Session state
_session_state = {}


# ---------------------------------------------------------------------------
# Gateway HTTP client (JSON-RPC over HTTP — no MCP library needed)
# ---------------------------------------------------------------------------

def _gateway_call(method: str, params: dict, jwt_token: str) -> dict:
    """Call the Gateway using MCP JSON-RPC protocol over HTTP."""
    response = requests.post(
        GATEWAY_ENDPOINT,
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        },
        timeout=30,
    )
    return response.json()


def _discover_tools(jwt_token: str) -> list:
    """Discover available tools from the Gateway."""
    result = _gateway_call("tools/list", {}, jwt_token)
    return result.get("result", {}).get("tools", [])


def _invoke_tool(tool_name: str, arguments: dict, jwt_token: str) -> str:
    """Invoke a tool via the Gateway and return the result as a string."""
    result = _gateway_call("tools/call", {"name": tool_name, "arguments": arguments}, jwt_token)
    mcp_result = result.get("result", {})
    if mcp_result.get("isError"):
        content = mcp_result.get("content", [{}])
        return json.dumps({"error": content[0].get("text", "Unknown error") if content else "Unknown error"})
    content = mcp_result.get("content", [])
    if content:
        text = content[0].get("text", json.dumps(mcp_result))
        # Parse Lambda proxy response format: {"statusCode":200,"body":"{...}"}
        try:
            parsed = json.loads(text)
            if "body" in parsed and "statusCode" in parsed:
                return parsed["body"]  # Return the inner body directly
        except (json.JSONDecodeError, TypeError):
            pass
        return text
    return json.dumps(mcp_result)


# ---------------------------------------------------------------------------
# Task 2.6 — Invoke agent with Gateway tools (HTTP-based)
# ---------------------------------------------------------------------------

def invoke_with_tools(tenant_id: str, jwt_token: str, message: str) -> dict:
    """
    Create a Strands Agent with Gateway tools and invoke it.

    Discovers tools from the Gateway, wraps each as a @tool function,
    creates the agent, and invokes it with the user's message.

    The JWT is passed to the Gateway for authentication.
    The Gateway validates it, applies Cedar policy, runs the interceptor,
    and routes to the appropriate target.

    Args:
        tenant_id: Tenant identifier (for response metadata).
        jwt_token: Tenant JWT for Gateway authentication.
        message: User's query.

    Returns:
        Dict with 'response', 'tenant_id', and 'tools_used'.
    """
    try:
        # TODO: Task 2.6 — Discover tools from Gateway, create agent, invoke
        raise NotImplementedError("Complete Task 2.6 — invoke_with_tools")  # REPLACE THIS LINE
    except Exception as e:
        import traceback
        app.logger.error(f"invoke_with_tools error: {type(e).__name__}: {e}")
        app.logger.error(traceback.format_exc())
        return {"error": str(e), "type": type(e).__name__, "tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# AgentCore Runtime entrypoint
# ---------------------------------------------------------------------------

@app.entrypoint
def invoke_agent(payload: dict, context) -> dict:
    """
    AgentCore Runtime entrypoint.

    Reads the message and JWT from the payload/headers,
    then invokes the agent with Gateway tools.
    """
    try:
        message = payload.get("message", payload.get("prompt", ""))
        if not message:
            return {"error": "message field required"}

        # Get JWT from request headers (forwarded by AgentCore)
        headers = context.request_headers or {}
        auth_header = headers.get("Authorization", "")
        jwt_token = auth_header.removeprefix("Bearer ").strip()

        # Decode tenant_id from JWT (or from payload for testing)
        tenant_id = payload.get("tenant_id", "")
        if not tenant_id and jwt_token:
            import base64
            try:
                part = jwt_token.split(".")[1]
                part += "=" * (-len(part) % 4)
                claims = json.loads(base64.urlsafe_b64decode(part))
                tenant_id = claims.get("custom:tenant_id", "")
            except Exception:
                pass

        if not tenant_id:
            return {"error": "tenant_id missing"}

        _session_state["invocation_count"] = _session_state.get("invocation_count", 0) + 1

        result = invoke_with_tools(tenant_id, jwt_token, message)
        result["invocation_count"] = _session_state["invocation_count"]
        return result
    except Exception as e:
        import traceback
        app.logger.error(f"invoke_agent error: {type(e).__name__}: {e}")
        app.logger.error(traceback.format_exc())
        return {"error": str(e), "type": type(e).__name__}


if __name__ == "__main__":
    app.run()
