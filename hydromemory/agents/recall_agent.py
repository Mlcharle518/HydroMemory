"""Recall Agent (PRD §8): surfaces the right memory at the right time.

Searches for candidate droplets, runs each through the §10 access check under the
requesting agent's identity, drops denied ones, and ranks the survivors.

Engine surface used:
    engine.search(query: dict) -> list[Droplet]
        Retrieve candidate droplets for a recall query.
    engine.check_access(droplet, agent, context, operation) -> AccessDecision
        Governance gate (delegated to the Privacy agent's authority in §8).
    engine.rank(droplets: list, query: dict) -> list[Droplet]
        Order the access-cleared candidates by recall relevance.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import AccessContext, Operation, TrustLevel


class RecallAgent(BaseAgent):
    """Search -> per-droplet access check -> rank."""

    name = "recall"
    trust_level = TrustLevel.APPROVED
    stages = ("recall",)

    def run(self, ctx: AgentContext) -> list[Any]:
        query = ctx.payload.get("query", {})
        access_ctx: AccessContext = ctx.payload.get("access_context") or AccessContext(
            recall_mode=ctx.payload.get("recall_mode"),
            safe_context=bool(ctx.payload.get("safe_context", False)),
        )
        identity = self.identity()

        candidates = self.engine.search(query)
        allowed: list[Any] = []
        for droplet in candidates:
            decision = self.engine.check_access(
                droplet, identity, access_ctx, Operation.READ
            )
            if decision.allowed:
                allowed.append(droplet)

        ranked = self.engine.rank(allowed, query)
        ctx.data["recalled"] = ranked
        return ranked
