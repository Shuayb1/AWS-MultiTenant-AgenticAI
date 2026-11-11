# SPDX-License-Identifier: MIT-0
"""
agent.py - Pooled Multi-Tenant Agent on AgentCore Runtime
Module 3, Lab 1

Phase 1: JWT-based invocation. The agent receives a Cognito JWT directly,
decodes it to extract tenant identity, and uses TVM credentials to scope
data access. AgentCore Runtime provides microVM-level session isolation.

Env vars:
  AGENTCORE_TVM_ROLE_ARN - IAM role ARN for TVM credential scoping
  AWS_REGION             - AWS region (default: us-east-1)
"""

import base64
import json
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_TVM_ROLE_ARN = os.environ.get("AGENTCORE_TVM_ROLE_ARN", "")

# Use direct model ID (not cross-region inference profile) for AgentCore compatibility
_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")

_model = BedrockModel(
    model_id=_MODEL_ID,
    region_name=_REGION,
)

SYSTEM_PROMPT = """You are a helpful multi-tenant assistant. You serve one tenant at a time.
Always acknowledge the tenant context and be helpful with their requests.
Never reveal information about other tenants."""

app = BedrockAgentCoreApp()


@app.ping
def health_check():
    from bedrock_agentcore.runtime import PingStatus
    return PingStatus.HEALTHY


# Singleton agent instance - persists within this microVM across invocations.
# This means conversation history is shared across all invocations to the same session.
_agent = Agent(model=_model, system_prompt=SYSTEM_PROMPT)

# Session state - persists within this microVM across invocations to the same session.
_session_state = {}


# ---------------------------------------------------------------------------
# Helper: JWT decode (signature validated by AgentCore Runtime)
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict:
    """Decode the JWT payload. AgentCore validates the signature - we just read claims."""
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


# ---------------------------------------------------------------------------
# Task 1.1 - Agent entrypoint (Phase 1: JWT-based)
# ---------------------------------------------------------------------------

@app.entrypoint
def invoke_agent(payload: dict, context) -> dict:
    """
    AgentCore Runtime entrypoint.

    Phase 1 (JWT-based): The agent receives the JWT in request headers,
    decodes it to extract tenant_id. Session state persists within the
    microVM across invocations.

    Args:
        payload: Request body with 'prompt' field.
        context: AgentCore Runtime context with request_headers and session_id.

    Returns:
        Dict with tenant_id, session_id, response, and invocation_count.
    """
    try:
        # TODO: Task 1.1 - Decode JWT, extract tenant_id, track session state, invoke agent
        # Step 1: Read JWT from request headers
        # Phase 1 (JWT auth): standard Authorization header
        # Phase 2 (IAM auth): custom header forwarded by backend Lambda
        headers = context.request_headers or {}
        auth_header = (
            headers.get("Authorization", "")
            or headers.get("authorization", "")
            or headers.get("x-amzn-bedrock-agentcore-runtime-custom-authorization", "")
            or headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization", "")
        )
        tenant_id = ""

        # Step 2: Decode JWT payload to extract tenant_id
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ")
            claims = _decode_jwt_payload(token)
            tenant_id = claims.get("custom:tenant_id", "")

        if not tenant_id:
            return {"error": "tenant_id missing - JWT required in Authorization or custom header"}

        # Step 3: Track per-session invocation count
        _session_state["invocation_count"] = _session_state.get("invocation_count", 0) + 1
        _session_state["tenant_id"] = tenant_id

        # Step 4: Invoke the agent and return response
        message = payload.get("prompt", payload.get("message", ""))
        response = _agent(message)

        return {
            "response": str(response),
            "tenant_id": tenant_id,
            "session_id": context.session_id,
            "invocation_count": _session_state["invocation_count"],
        }
    except Exception as e:
        import traceback
        app.logger.error(f"invoke_agent error: {type(e).__name__}: {e}")
        app.logger.error(traceback.format_exc())
        return {"error": str(e), "type": type(e).__name__}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
