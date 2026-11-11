# SPDX-License-Identifier: MIT-0
"""
agent.py — Multi-tenant agent with S3 Vector long-term memory
Module 3, Lab 4

AgentCore Runtime provides session isolation at the infrastructure level:
each runtimeSessionId is routed to a dedicated microVM. All turns of a
conversation arrive at the SAME microVM, so agent.messages is maintained
in memory across turns automatically.

The plugin uses len(agent.messages) == 0 to detect the first turn and
retrieves long-term memories from S3 Vectors via semantic search.

Tenant identity is extracted from the JWT bearer token forwarded by
AgentCore Runtime's built-in JWT authorizer.

Env vars: S3_VECTOR_BUCKET_NAME, S3_VECTOR_TVM_ROLE_ARN, AWS_REGION, BEDROCK_MODEL_ID
"""

import base64
import json
import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel
from strands_s3_vectors_memory import MultiTenantS3VectorMemory, S3VectorMemoryPlugin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")

BASE_PROMPT = """You are a helpful assistant.
{memory_context}
Use prior context naturally in your responses without explicitly announcing
that you are recalling past information, unless the user asks."""

# ---------------------------------------------------------------------------
# Module-level singletons (initialized during container startup)
# ---------------------------------------------------------------------------

# TODO: Task 4.1 - instantiate MultiTenantS3VectorMemory
_store = MultiTenantS3VectorMemory(
    bucket_name  = os.environ["S3_VECTOR_BUCKET_NAME"],
    tvm_role_arn = os.environ.get("S3_VECTOR_TVM_ROLE_ARN"),
)

# TODO: Task 4.2 - instantiate S3VectorMemoryPlugin
_plugin = S3VectorMemoryPlugin(store=_store, base_prompt=BASE_PROMPT)

# TODO: Task 4.3 - create the Agent singleton with plugins
_agent = Agent(
    model            = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION),
    name             = "assistant",
    plugins          = [_plugin],
    tools            = [_plugin.memory_tool],
    system_prompt    = BASE_PROMPT,
    callback_handler = None,
)


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload - signature already verified by AgentCore Runtime."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# AgentCore Runtime app
# ---------------------------------------------------------------------------

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke_agent(payload: dict, context) -> dict:
    """
    Called by AgentCore Runtime for every /invocations request.

    context.session_id      - runtimeSessionId (scoped to this conversation)
    context.request_headers - forwarded HTTP headers including Authorization JWT
    """
    try:
        conversation_id = context.session_id or "default"
        headers         = context.request_headers or {}
        auth_header     = headers.get("Authorization") or headers.get("authorization") or ""
        message         = payload.get("message", payload.get("prompt", ""))
        end_session     = payload.get("end_session", False)

        if _agent is None:
            return {"error": "Complete Tasks 4.1-4.3 to initialize the agent", "type": "NotImplementedError"}

        # Extract tenant identity from JWT
        claims    = _decode_jwt_payload(auth_header.removeprefix("Bearer "))
        tenant_id = claims.get("custom:tenant_id", "")
        user_id   = claims.get("sub", conversation_id)

        if not tenant_id:
            return {"error": "JWT missing custom:tenant_id claim"}

        tenant_context = {
            "tenantId":   tenant_id,
            "tenantName": claims.get("custom:tenant_name", ""),
            "tier":       claims.get("custom:tier", "standard"),
            "status":     "active",
            "features":   [],
            "limits":     {},
        }

        # Detect conversation change (needed for local testing only;
        # on AgentCore Runtime each conversation gets its own microVM)
        if getattr(_agent, "_current_conv_id", None) != conversation_id:
            _agent.messages = []
            _agent._current_conv_id = conversation_id

        response = _agent(
            message,
            invocation_state={
                "tenant_context":  tenant_context,
                "user_id":         user_id,
                "conversation_id": conversation_id,
                "end_session":     end_session,
            },
        )
        response_str = str(response)

        logger.info(
            "[request] tenant=%s conv=%s end_session=%s\n  USER : %s\n  AGENT: %s",
            tenant_id, conversation_id, end_session,
            message[:200], response_str[:200],
        )

        return {
            "response":        response_str,
            "tenant_id":       tenant_id,
            "conversation_id": conversation_id,
        }
    except Exception as e:
        import traceback
        logger.error(f"invoke_agent error: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        return {"error": str(e), "type": type(e).__name__}


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8080")))
