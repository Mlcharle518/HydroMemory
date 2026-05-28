"""Distillation Agent (PRD §8): converts many memories into principles, values,
and operating preferences (the §12 Example B "preference becomes principle" path).

Clusters related droplets, then distills each cluster into a single principle
droplet.

Engine surface used:
    engine.cluster(droplets: list, context: dict) -> list[cluster]
        Group related droplets into clusters.
    engine.distill(cluster) -> Droplet
        Derive a principle droplet from a cluster of related memories.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import TrustLevel


class DistillationAgent(BaseAgent):
    """Clusters droplets and distills each cluster into a principle."""

    name = "distillation"
    trust_level = TrustLevel.HIGH_TRUST
    stages = ("distill",)

    def run(self, ctx: AgentContext) -> list[Any]:
        droplets = ctx.payload.get("droplets", [])
        clusters = self.engine.cluster(droplets, ctx.payload)
        principles = [self.engine.distill(cluster) for cluster in clusters]
        ctx.data["principles"] = principles
        return principles
