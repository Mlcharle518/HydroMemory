"""Privacy Agent (PRD §8): controls access, consent, sensitivity, and scope.

Owns the governance gate. For exposure decisions it runs ``check_access`` for the
``EXPOSE_TO_USER`` operation and computes a ``privacy_risk`` score the caller can
threshold. It is the authority other roles defer to for consent.

Engine surface used:
    engine.check_access(droplet, agent, context, operation) -> AccessDecision
    engine.privacy_risk(droplet, context) -> float
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.governance import AccessContext, Operation, TrustLevel


class PrivacyAgent(BaseAgent):
    """Vets droplets for exposure: access decision + privacy-risk score."""

    name = "privacy"
    trust_level = TrustLevel.HIGH_TRUST
    is_user_proxy = True
    stages = ("expose", "recall")

    def run(self, ctx: AgentContext) -> list[dict[str, Any]]:
        droplets = ctx.payload.get("droplets") or ctx.data.get("recalled", [])
        access_ctx: AccessContext = ctx.payload.get("access_context") or AccessContext(
            consent_granted=bool(ctx.payload.get("consent_granted", False)),
            thaw_granted=bool(ctx.payload.get("thaw_granted", False)),
            safe_context=bool(ctx.payload.get("safe_context", False)),
        )
        identity = self.identity()

        vetted: list[dict[str, Any]] = []
        for droplet in droplets:
            decision = self.engine.check_access(
                droplet, identity, access_ctx, Operation.EXPOSE_TO_USER
            )
            risk = self.engine.privacy_risk(droplet, access_ctx)
            vetted.append({"droplet": droplet, "decision": decision, "privacy_risk": risk})
        ctx.data["vetted"] = vetted
        return vetted
