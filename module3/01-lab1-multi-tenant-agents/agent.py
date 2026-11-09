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
        raise NotImplementedError("Complete Task 1.1 - invoke_agent")  # REPLACE THIS LINE
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
