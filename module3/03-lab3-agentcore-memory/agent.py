# SPDX-License-Identifier: MIT-0
"""
agent.py — AgentCore Runtime entrypoint for Bedrock AgentCore Memory
Module 3, Lab 3

_session_manager, _model, and _agent are module-level singletons.
MultiTenantAgentCoreMemorySessionManager registers a BeforeInvocationEvent hook
that reads tenant identity from invocation_state and updates config and
boto3 clients before each request — no separate plugin needed.

Isolation layers:
  Application : actor_id/session_id embed tenantId
  IAM         : TVM (STS AssumeRole + TenantID session tag)

Env vars: AGENTCORE_MEMORY_ID, AGENTCORE_TVM_ROLE_ARN, AWS_REGION
"""

import base64
import json
import os

from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agentcore_memory_session_manager import MultiTenantAgentCoreMemorySessionManager

_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Module-level initialization (runs during container startup, not handler timeout)
_session_manager = MultiTenantAgentCoreMemorySessionManager(
    memory_id=os.environ["AGENTCORE_MEMORY_ID"],
    region_name=_REGION,
)
_model = BedrockModel(
    model_id=os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0"),
    region_name=_REGION,
)
_agent = Agent(
    model=_model,
    system_prompt="You are a helpful assistant.",
    session_manager=_session_manager,
)

app = BedrockAgentCoreApp()


@app.ping
def health_check():
    from bedrock_agentcore.runtime import PingStatus
    return PingStatus.HEALTHY


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without signature verification (AgentCore Runtime validates first)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _extract_tenant_context(context) -> dict | None:
    """
    Extract and validate tenant identity from the AgentCore request context.

    Returns a dict with tenantId, user_id, or None if the JWT is missing or
    the tenant_id claim is absent.
    """
    auth_header = (context.request_headers or {}).get("Authorization", "")
    token       = auth_header.removeprefix("Bearer ")
    claims      = _decode_jwt_payload(token)
    tenant_id   = claims.get("custom:tenant_id", "")
    if not tenant_id:
        return None
    return {
        "tenant_context": {"tenantId": tenant_id},
        # user_id always from JWT sub — never overridable by payload
        "user_id": claims.get("sub", "unknown"),
    }


@app.entrypoint
def invoke_agent(payload: dict, context) -> dict:
    try:
        identity = _extract_tenant_context(context)
        if not identity:
            return {"error": "tenant_id missing — JWT required"}

        agent = _agent
        response = agent(
            payload.get("message", ""),
            invocation_state={
                **identity,
                "conversation_id": payload.get("conversation_id", "default"),
            },
        )
        return {"response": str(response), "tenant_id": identity["tenant_context"]["tenantId"]}
    except Exception as e:
        import traceback
        app.logger.error(f"invoke_agent error: {type(e).__name__}: {e}")
        app.logger.error(traceback.format_exc())
        return {"error": str(e), "type": type(e).__name__}


if __name__ == "__main__":
    app.run()
