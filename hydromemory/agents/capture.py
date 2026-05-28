"""Capture Agent (PRD §8): observes events and proposes new droplets.

Engine surface used:
    engine.propose_droplet(event: dict) -> Droplet
        Encode a raw observed event into a candidate droplet (the ABSORB path).
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import TrustLevel


class CaptureAgent(BaseAgent):
    """Turns raw events on ``ctx.payload['events']`` into proposed droplets."""

    name = "capture"
    trust_level = TrustLevel.SESSION
    stages = ("capture",)

    def run(self, ctx: AgentContext) -> list[Any]:
        events = ctx.payload.get("events", [])
        proposed = [self.engine.propose_droplet(event) for event in events]
        ctx.data.setdefault("proposed", []).extend(proposed)
        return proposed
