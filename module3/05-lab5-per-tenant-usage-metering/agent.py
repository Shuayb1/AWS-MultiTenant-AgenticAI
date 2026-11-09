# SPDX-License-Identifier: MIT-0
"""
agent.py - Pooled Multi-Tenant Agent with Per-Tenant Metering
Module 3, Lab 5

Deploys a simple agent with TenantMeteringPlugin attached.
The plugin hooks into the Strands agent lifecycle and emits per-tenant
CloudWatch metrics automatically - no changes to business logic.

Env vars:
  AWS_REGION       - AWS region (default: us-east-1)
  BEDROCK_MODEL_ID - Model ID (default: amazon.nova-pro-v1:0)
"""

import base64
import json
import os
import traceback

from bedrock_agentcore.runtime import BedrockAgentCoreApp, PingStatus
from strands import Agent
from strands.models.bedrock import BedrockModel

from tenant_metering_plugin import TenantMeteringPlugin

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")

_model = BedrockModel(model_id=_MODEL_ID, region_name=_REGION)

SYSTEM_PROMPT = """You are a helpful multi-tenant assistant. You serve one tenant at a time.
Always acknowledge the tenant context and be helpful with their requests.
Do not include thinking tags or internal reasoning in your response. Respond directly and concisely."""

app = BedrockAgentCoreApp()


@app.ping
def health_check():
    return PingStatus.HEALTHY


# Session state
_session_state = {}


# ---------------------------------------------------------------------------
# Helper: JWT decode
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict:
    """Decode the JWT payload. AgentCore validates the signature."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Agent entrypoint with metering
# ---------------------------------------------------------------------------

@app.entrypoint
def invoke_agent(payload: dict, context) -> dict:
    """
    AgentCore Runtime entrypoint with per-tenant metering.

    The TenantMeteringPlugin attaches to the agent and automatically
    emits CloudWatch EMF metrics for every LLM call and tool invocation.
    """
    try:
        # Extract tenant identity from JWT
        headers = context.request_headers or {}
        auth_header = headers.get("Authorization", headers.get("authorization", ""))
        token = auth_header.removeprefix("Bearer ").strip()

        claims = _decode_jwt_payload(token)
        tenant_id = claims.get("custom:tenant_id", "")

        if not tenant_id:
            return {"error": "tenant_id missing - JWT required"}

        # Track session state
        _session_state["invocation_count"] = _session_state.get("invocation_count", 0) + 1
        _session_state["tenant_id"] = tenant_id

        message = payload.get("message", payload.get("prompt", ""))
        if not message:
            return {"error": "message field required"}

        # TODO: Task 5.4 - Attach the TenantMeteringPlugin
        metering_plugins = []  # REPLACE THIS LINE

        agent = Agent(model=_model, system_prompt=SYSTEM_PROMPT, plugins=metering_plugins)
        response = agent(message)

        return {
            "response": str(response),
            "tenant_id": tenant_id,
            "session_id": getattr(context, "session_id", None) or "no-session",
            "invocation_count": _session_state["invocation_count"],
        }
    except (NotImplementedError, Exception) as e:
        app.logger.error(f"invoke_agent error: {type(e).__name__}: {e}")
        app.logger.error(traceback.format_exc())
        return {"error": str(e), "type": type(e).__name__}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
