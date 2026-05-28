"""Governance enforcement entry point (PRD §10).

``check_access`` is the single function recall and every mutating verb call before
exposing or changing a droplet. The decision is the logical AND of two layers:

1. the reservoir rule (PRD §10, loaded from ``policy_data.json`` via
   :mod:`hydromemory.governance.policy`), and
2. the droplet's own :class:`~hydromemory.schema.Permissions`.

Obligations (explanation / thaw / consent / overwrite-blocked) are *returned*,
never auto-applied: the caller satisfies them before proceeding. ``check_access``
denies eagerly only for hard gates (wrong trust level, filtration-only
reservoir, blocked external sharing, glacier without thaw/consent, sacred
overwrite); softer requirements surface as obligations on an allowed decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hydromemory.governance.obligations import AccessDecision, Obligation, Operation
from hydromemory.governance.policy import AccessLevel, ReservoirRule, rule_for
from hydromemory.schema import Droplet, Visibility


class TrustLevel(str, Enum):
    SESSION = "session"
    APPROVED = "approved"
    HIGH_TRUST = "high_trust"


# Ordering used to compare an agent's trust against a reservoir's requirement.
_TRUST_RANK: dict[TrustLevel, int] = {
    TrustLevel.SESSION: 0,
    TrustLevel.APPROVED: 1,
    TrustLevel.HIGH_TRUST: 2,
}

# Minimum trust an access level requires (consent/thaw gates handled separately).
_ACCESS_MIN_TRUST: dict[AccessLevel, TrustLevel] = {
    AccessLevel.SESSION_AGENTS: TrustLevel.SESSION,
    AccessLevel.APPROVED_AGENTS: TrustLevel.APPROVED,
    AccessLevel.HIGH_TRUST_AGENTS_ONLY: TrustLevel.HIGH_TRUST,
    AccessLevel.EXPLICIT_USER_CONSENT: TrustLevel.SESSION,
    AccessLevel.FILTRATION_AGENT_ONLY: TrustLevel.SESSION,
    AccessLevel.EXPLICIT_USER_CONSENT_OR_USER_DEFINED_CORE_BEHAVIOR: TrustLevel.SESSION,
}

# Operations that change a droplet's content/state (vs. pure reads).
_MUTATING_OPS: frozenset[Operation] = frozenset(
    {Operation.MUTATE, Operation.TRANSFORM, Operation.OVERWRITE}
)


@dataclass
class AgentIdentity:
    name: str
    trust_level: TrustLevel = TrustLevel.SESSION
    is_filtration: bool = False
    is_user_proxy: bool = False


@dataclass
class AccessContext:
    recall_mode: str | None = None
    safe_context: bool = False
    consent_granted: bool = False
    thaw_granted: bool = False


def _trust_ok(agent: AgentIdentity, rule: ReservoirRule) -> bool:
    """Whether ``agent`` clears the reservoir's minimum trust level."""
    required = _ACCESS_MIN_TRUST.get(rule.access_level, TrustLevel.APPROVED)
    return _TRUST_RANK[agent.trust_level] >= _TRUST_RANK[required]


def _agent_in_permissions(droplet: Droplet, agent: AgentIdentity) -> bool:
    """Whether the droplet's ``allowed_agents`` admit this agent.

    An empty ``allowed_agents`` list means "no per-droplet restriction". A
    user-proxy agent (acting directly for the owner) is always admitted.
    """
    allowed = droplet.permissions.allowed_agents
    if agent.is_user_proxy:
        return True
    if not allowed:
        return True
    return agent.name in allowed


def check_access(
    droplet: Droplet,
    agent: AgentIdentity,
    context: AccessContext,
    operation: Operation,
) -> AccessDecision:
    """Decide whether ``agent`` may perform ``operation`` on ``droplet``.

    Returns an :class:`AccessDecision` (allow/deny + obligations + usability).
    The decision ANDs the reservoir rule (§10) with the droplet permissions.
    """
    rule = rule_for(droplet.reservoir)
    obligations: list[Obligation] = []

    # ``usable_for_generation`` reflects both the reservoir policy and any
    # per-droplet meta flag a contamination/lifecycle step may have set.
    meta_usable = droplet.meta.get("usable_for_generation", True)
    usable_for_generation = bool(rule.usable_for_response) and bool(meta_usable)

    def deny(reason: str) -> AccessDecision:
        return AccessDecision(
            allowed=False,
            denial_reason=reason,
            obligations=obligations,
            usable_for_generation=usable_for_generation,
        )

    # --- Hard gate: contaminated reservoir is filtration-agent-only. ---------
    if rule.access_level is AccessLevel.FILTRATION_AGENT_ONLY:
        usable_for_generation = False
        if not agent.is_filtration:
            return deny("reservoir is contaminated: filtration agent only")

    # --- Hard gate: per-droplet allowed_agents. ------------------------------
    if not _agent_in_permissions(droplet, agent):
        return deny(f"agent '{agent.name}' not in droplet allowed_agents")

    # --- Hard gate: trust level required by the reservoir. -------------------
    # Filtration agents are exempt from the trust floor *for the contaminated
    # reservoir only* (they have already passed the filtration-only gate above).
    if not (agent.is_filtration and rule.access_level is AccessLevel.FILTRATION_AGENT_ONLY):
        if not _trust_ok(agent, rule):
            required = _ACCESS_MIN_TRUST.get(rule.access_level, TrustLevel.APPROVED)
            return deny(
                f"reservoir requires trust >= {required.value}; "
                f"agent has {agent.trust_level.value}"
            )

    # --- Groundwater READ requires an explanation (soft obligation). ---------
    if rule.requires_explanation:
        obligations.append(Obligation.REQUIRES_EXPLANATION)

    # --- Glacier: thaw + consent gates. --------------------------------------
    if rule.requires_thaw_protocol:
        obligations.append(Obligation.REQUIRES_THAW)
        if not context.consent_granted:
            obligations.append(Obligation.REQUIRES_CONSENT)
        if not context.thaw_granted:
            return deny("glacier reservoir requires thaw protocol (not granted)")
        if not context.consent_granted:
            return deny("glacier reservoir requires explicit user consent (not granted)")

    # --- Sacred: explicit consent unless acting as user-defined core. --------
    if rule.access_level is AccessLevel.EXPLICIT_USER_CONSENT_OR_USER_DEFINED_CORE_BEHAVIOR:
        if not (context.consent_granted or agent.is_user_proxy):
            obligations.append(Obligation.REQUIRES_CONSENT)
            return deny("sacred reservoir requires explicit user consent")

    # --- Overwrite protection (sacred and any rule with overwrite disabled). -
    if operation in (Operation.OVERWRITE, Operation.MUTATE, Operation.TRANSFORM):
        if not rule.overwrite_allowed:
            obligations.append(Obligation.OVERWRITE_BLOCKED)
            if operation is Operation.OVERWRITE:
                return deny("overwrite blocked for this reservoir (e.g. sacred)")

    # --- Operation-specific gates. -------------------------------------------
    if operation is Operation.EXPOSE_TO_USER:
        if not rule.user_visible:
            return deny("reservoir is not user-visible")
        # A private droplet may only be exposed to its owner (user proxy).
        if droplet.permissions.visibility is Visibility.PRIVATE and not agent.is_user_proxy:
            return deny("private droplet may only be exposed to its owner")

    if operation is Operation.USE_FOR_GENERATION and not usable_for_generation:
        return deny("droplet is not usable for generation (policy or contamination)")

    # --- External sharing gate (mutations that imply leaving the vault). -----
    if operation in _MUTATING_OPS and not droplet.permissions.external_sharing:
        # External sharing being off does not block in-vault mutation; it is
        # surfaced via permission_score / privacy_risk and consent obligations.
        if droplet.permissions.requires_consent_for_external_use and not context.consent_granted:
            if Obligation.REQUIRES_CONSENT not in obligations:
                obligations.append(Obligation.REQUIRES_CONSENT)

    return AccessDecision(
        allowed=True,
        denial_reason=None,
        obligations=obligations,
        usable_for_generation=usable_for_generation,
    )
