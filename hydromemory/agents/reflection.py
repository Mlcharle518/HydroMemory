"""Reflection Agent (PRD §8): periodically asks whether memories are still accurate.

Pulls aged droplets and re-verifies each, letting the engine update confidence /
``cycle.last_verified`` or flag stale memory for filtration.

Engine surface used:
    engine.aged_droplets(context: dict) -> list[Droplet]
        Droplets due for re-verification (e.g. not verified in N cycles).
    engine.reverify(droplet) -> Droplet
        Re-check a droplet against current knowledge and update it.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import TrustLevel


class ReflectionAgent(BaseAgent):
    """Re-verifies aged droplets so stale memory does not masquerade as fact."""

    name = "reflection"
    trust_level = TrustLevel.APPROVED
    stages = ("reflect", "maintain")

    def run(self, ctx: AgentContext) -> list[Any]:
        aged = ctx.payload.get("droplets")
        if aged is None:
            aged = self.engine.aged_droplets(ctx.payload)
        reverified = [self.engine.reverify(droplet) for droplet in aged]
        ctx.data["reverified"] = reverified
        return reverified
