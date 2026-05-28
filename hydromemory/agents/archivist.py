"""Archivist Agent (PRD §8): handles preservation, freezing, versioning, and
deletion — the §11 forgetting verbs applied as policy decisions.

Each droplet on the payload carries an ``action`` (in ``meta['archive_action']``
or via a parallel ``actions`` map) telling the archivist what to do: ``freeze``
(seal), ``sediment``, or ``delete``.

Engine surface used:
    engine.freeze(droplet) -> Droplet      # seal into glacier (§11 Sealing)
    engine.sediment(droplet) -> Droplet    # sink to archive (§11 Sedimentation)
    engine.delete(droplet) -> None         # hard removal (§11 Deletion)
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import TrustLevel


class ArchivistAgent(BaseAgent):
    """Dispatches preservation/forgetting actions to the engine's verbs."""

    name = "archivist"
    trust_level = TrustLevel.HIGH_TRUST
    stages = ("archive", "maintain")

    def _action_for(self, droplet: Any, ctx: AgentContext) -> str:
        actions = ctx.payload.get("actions") or {}
        if getattr(droplet, "id", None) in actions:
            return actions[droplet.id]
        meta = getattr(droplet, "meta", {}) or {}
        return meta.get("archive_action", "sediment")

    def run(self, ctx: AgentContext) -> list[Any]:
        droplets = ctx.payload.get("droplets", [])
        results: list[Any] = []
        for droplet in droplets:
            action = self._action_for(droplet, ctx)
            if action in ("freeze", "seal"):
                results.append(self.engine.freeze(droplet))
            elif action == "delete":
                results.append(self.engine.delete(droplet))
            else:
                results.append(self.engine.sediment(droplet))
        ctx.data["archived"] = results
        return results
