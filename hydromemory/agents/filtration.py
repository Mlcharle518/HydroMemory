"""Filtration Agent (PRD §8, §10.1): detects contradictions, contamination,
hallucination, and outdated memory, then routes or repairs.

This is the only role that may touch the ``contaminated`` reservoir (its
identity carries ``is_filtration=True``).

Engine surface used:
    engine.assess_and_route(droplet, context: dict) -> Droplet
        Run contamination detection and route a polluted droplet to the
        contaminated reservoir (see ``hydromemory.contamination``).
    engine.filter(droplet) -> Droplet
        Reframe a polluted droplet into a usable, filtered droplet.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import TrustLevel
from hydromemory.schema import Phase


class FiltrationAgent(BaseAgent):
    """Assesses droplets for contamination and filters already-polluted ones."""

    name = "filtration"
    trust_level = TrustLevel.HIGH_TRUST
    is_filtration = True
    stages = ("filter", "maintain", "capture")

    def run(self, ctx: AgentContext) -> list[Any]:
        droplets = ctx.payload.get("droplets") or ctx.data.get("proposed", [])
        out: list[Any] = []
        for droplet in droplets:
            if getattr(droplet, "phase", None) is Phase.POLLUTED:
                # Already flagged polluted -> repair it into a filtered droplet.
                out.append(self.engine.filter(droplet))
            else:
                # Otherwise assess and route if the detector flags contamination.
                out.append(self.engine.assess_and_route(droplet, ctx.payload))
        return out
