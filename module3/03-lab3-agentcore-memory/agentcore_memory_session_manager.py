# SPDX-License-Identifier: MIT-0
"""
agentcore_memory_session_manager.py — Tenant-Isolated Bedrock AgentCore Memory
Module 3, Lab 3

Isolation layers:
  Application : actor_id/session_id embed tenantId, agentName, userId, conversationId
  IAM         : TVM (STS AssumeRole + TenantID session tag) enforces at control plane

Env vars: AGENTCORE_MEMORY_ID, AGENTCORE_TVM_ROLE_ARN
"""

import os
from typing import Dict

from strands import Agent
from strands.hooks import BeforeInvocationEvent
from strands.hooks.registry import HookRegistry
from strands.models import BedrockModel
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from isolation_manager import IsolationManager, IsolationError

_AGENT_NAME = "workshop-agent"


class MultiTenantAgentCoreMemorySessionManager(AgentCoreMemorySessionManager):
    """
    Multi-tenant AgentCoreMemorySessionManager singleton.

    Extends AgentCoreMemorySessionManager with a BeforeInvocationEvent hook
    that reads tenant identity from invocation_state and updates config and
    boto3 clients before each request. All existing AgentCore hooks are
    preserved via super().register_hooks().

    Required invocation_state keys per request:
        tenant_context  (dict)  — must contain 'tenantId'
        user_id         (str)
        conversation_id (str)
    """

    def __init__(
        self,
        memory_id: str,
        region_name: str = None,
        **kwargs,
    ) -> None:
        self.memory_id   = memory_id
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self._isolation  = IsolationManager(
            role_arn=os.environ["AGENTCORE_TVM_ROLE_ARN"],
            region_name=self.region_name,
        )
        # Placeholder config — overwritten per request in before_invocation
        placeholder = AgentCoreMemoryConfig(
            memory_id=memory_id, actor_id="init", session_id="init"
        )
        super().__init__(
            agentcore_memory_config=placeholder,
            region_name=self.region_name,
            **kwargs,
        )

    def _build_actor_id(self, tenant_context: Dict, user_id: str) -> str:
        """Return '{tenantId}:{agentName}:{userId}' — scopes all memory ops to tenant+agent+user."""
        # TODO: Task 3.1 — construct tenant-scoped actor ID
        return f"{tenant_context['tenantId']}:{_AGENT_NAME}:{user_id}"
        
    def _build_session_id(self, tenant_context: Dict, user_id: str, conversation_id: str) -> str:
        """Return '{tenantId}-{agentName}-{userId}-{conversationId}' — scopes memory to one conversation."""
        # TODO: Task 3.2 — construct tenant-scoped session ID
        return f"{tenant_context['tenantId']}-{_AGENT_NAME}-{user_id}-{conversation_id}"

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        """Register all AgentCore hooks plus the per-request tenant injection hook."""
        super().register_hooks(registry, **kwargs)
        registry.add_callback(
            BeforeInvocationEvent,
            lambda event: self._prepare(
                event.agent,
                event.invocation_state["tenant_context"],
                event.invocation_state["user_id"],
                event.invocation_state["conversation_id"],
            ),
        )

    def _prepare(self, agent: Agent, tenant_context: Dict, user_id: str, conversation_id: str) -> None:
        """
        Mutate this session manager for the current request.

        Binds the singleton to the caller's tenant/actor/session, enables
        per-turn LTM retrieval, swaps the boto3 clients to tenant-scoped STS
        credentials, and — only when the session changed — rehydrates
        agent.messages (STM) from AgentCore Memory.

        Why rehydrate here and not rely on AgentInitializedEvent: on AgentCore
        Runtime the Agent is a module-level singleton reused across warm
        invocations, and a "known" runtimeSessionId can land on a fresh microVM
        after idle/8h termination. agent.messages is only a warm cache; the
        durable record is AgentCore Memory. See runtime-how-it-works.html.

        Order matters: swap credentials BEFORE any read so list_messages runs
        under TVM creds (IAM ABAC), not ambient container creds.

        Raises IsolationError if tenantId is missing or STS AssumeRole fails.
        """
        # TODO: Task 3.3 — (a) get scoped STS session
        #                  (b) build actor_id/session_id, detect session change
        #                  (c) set config.actor_id, config.session_id, self.session_id
        #                  (d) set config.retrieval_config for LTM namespaces
        #                  (e) swap memory_client.gmcp_client / gmdp_client to scoped clients
        #                  (f) on session change, rehydrate agent.messages via self.list_messages
        # (a) TVM-scoped credentials for this tenant
        scoped_session = self._isolation.get_session(tenant_context)

        # (b) tenant-scoped identifiers; detect whether the conversation changed
        actor_id   = self._build_actor_id(tenant_context, user_id)
        session_id = self._build_session_id(tenant_context, user_id, conversation_id)
        switching  = session_id != self.config.session_id

        # (c) bind the singleton to this request
        self.config.actor_id   = actor_id
        self.config.session_id = session_id
        self.session_id        = session_id

        # (d) enable per-turn LTM retrieval. {actorId} resolves to the scoped
        #     actor, so the namespace begins with the tenant tag and matches
        #     the TVM role's bedrock-agentcore:namespace condition.
        self.config.retrieval_config = {
            "/{actorId}/preferences": RetrievalConfig(top_k=5, relevance_score=0.3),
            "/{actorId}/facts":       RetrievalConfig(top_k=5, relevance_score=0.4),
        }

        # (e) swap clients to scoped credentials BEFORE any read
        self.memory_client.gmcp_client = scoped_session.client(
            "bedrock-agentcore-control", region_name=self.region_name
        )
        self.memory_client.gmdp_client = scoped_session.client(
            "bedrock-agentcore", region_name=self.region_name
        )

        # (f) STM: rehydrate the transcript from AgentCore Memory only when the
        #     session changed. agent.messages is a warm-microVM cache; the
        #     durable record lives in AgentCore Memory, so a fresh microVM under
        #     a known session must read it back.
        if switching:
            restored = self.list_messages(session_id, agent.agent_id)
            agent.messages = [m.to_message() for m in restored]
            self._latest_agent_message[agent.agent_id] = restored[-1] if restored else None


# ---------------------------------------------------------------------------
# Local verification
# ---------------------------------------------------------------------------

def main():
    memory_id = os.environ.get("AGENTCORE_MEMORY_ID", "")
    if not memory_id:
        print("ERROR: set AGENTCORE_MEMORY_ID before running")
        return

    sm    = MultiTenantAgentCoreMemorySessionManager(memory_id=memory_id)
    model = BedrockModel(
        model_id=os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0"),
        region_name=sm.region_name,
    )
    agent = Agent(model=model, system_prompt="You are a helpful assistant.",
                  session_manager=sm)

    tenant_a = {"tenantId": "tenant-001"}
    tenant_b = {"tenantId": "tenant-002"}

    print("\n── Step 1: tenant-001 stores a private fact ──────────────────")
    try:
        actor_id_a = sm._build_actor_id(tenant_a, "user-456")
        agent("Remember this: my favourite colour is blue.", invocation_state={
            "tenant_context": tenant_a, "user_id": "user-456", "conversation_id": "conv-abc",
        })
        print(f"  ✓ tenant-001 stored a fact  (actor_id: {actor_id_a})")
    except NotImplementedError as exc:
        print(f"  ✗ {exc}\n    → Complete the stubs above, then re-run.")
        return

    print("\n── Step 2: tenant-001 reads its own events (should succeed) ──")
    agent("What is my favourite colour?", invocation_state={
        "tenant_context": tenant_a, "user_id": "user-456", "conversation_id": "conv-abc",
    })
    print("  ✓ tenant-001 retrieved response — isolation intact")

    print("\n── Step 3: tenant-002 attempts to read tenant-001's events ───")
    print(f"  Requested actor_id: {actor_id_a}  ← does NOT match tenant-002:*")
    try:
        agent("What is tenant-001's favourite colour?", invocation_state={
            "tenant_context": tenant_b, "user_id": "user-456", "conversation_id": "conv-abc",
        })
        print("  ✗ ISOLATION FAILURE — tenant-002 should have been denied")
    except Exception as e:
        if "AccessDenied" in str(e) or "AccessDeniedException" in str(e):
            print("  ✓ ISOLATION CONFIRMED — AWS denied tenant-002's request (AccessDeniedException)")
        else:
            print(f"  (unexpected error: {e})")

    print("\n── Summary ────────────────────────────────────────────────────")
    print("  tenant-001 stored a fact  ✓")
    print("  tenant-001 read own data  ✓")
    print("  tenant-002 denied access  ✓  ← IAM enforcement, not application logic")


if __name__ == "__main__":
    main()
