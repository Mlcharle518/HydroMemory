"""Hydrologist Agent (PRD §8): maintains phase transitions and cycle logic.

Engine surface used:
    engine.detect_triggers(droplet, context: dict) -> list[transition]
        Identify which lifecycle transitions a droplet is currently eligible for.
    engine.apply_transition(droplet, transition) -> Droplet
        Apply a single transition (phase/reservoir/state change) to a droplet.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import TrustLevel


class HydrologistAgent(BaseAgent):
    """Detects triggers on candidate droplets and applies their transitions."""

    name = "hydrologist"
    trust_level = TrustLevel.APPROVED
    stages = ("maintain", "capture")

    def run(self, ctx: AgentContext) -> list[Any]:
        droplets = ctx.payload.get("droplets") or ctx.data.get("proposed", [])
        transitioned: list[Any] = []
        for droplet in droplets:
            triggers = self.engine.detect_triggers(droplet, ctx.payload)
            for transition in triggers:
                droplet = self.engine.apply_transition(droplet, transition)
            transitioned.append(droplet)
        return transitioned
