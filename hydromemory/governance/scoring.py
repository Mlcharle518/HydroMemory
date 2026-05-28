"""Governance scoring: ``permission_score`` and ``privacy_risk`` (PRD §10, §16).

These are continuous companions to the boolean :func:`check_access`. The recall
ranker (Track B) folds ``permission_score`` into its ordering so cleanly-allowed
droplets surface before gated ones, and uses ``privacy_risk`` to keep sensitive
private memory out of casual exposure.

Both return values in ``[0, 1]`` and are intentionally monotone:

* ``permission_score`` -> 1.0 when an agent is cleanly allowed (no obligations,
  trust to spare), decreasing as obligations and trust gaps accrue, and 0.0 when
  access is denied outright.
* ``privacy_risk`` -> rises with private visibility, low confidence (a proxy for
  factual uncertainty per the §16 "do not treat high emotional charge as high
  factual confidence" guidance) and reservoir sensitivity.
"""
from __future__ import annotations

from hydromemory.governance.enforcement import (
    AccessContext,
    AgentIdentity,
    TrustLevel,
    check_access,
)
from hydromemory.governance.obligations import Obligation, Operation
from hydromemory.governance.policy import AccessLevel, rule_for
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Visibility

# Local trust ordering (mirrors enforcement's gate) for the trust-headroom reward.
_TRUST_RANK: dict[TrustLevel, int] = {
    TrustLevel.SESSION: 0,
    TrustLevel.APPROVED: 1,
    TrustLevel.HIGH_TRUST: 2,
}

# Per-obligation penalties subtracted from a clean 1.0 permission score.
_OBLIGATION_PENALTY: dict[Obligation, float] = {
    Obligation.REQUIRES_EXPLANATION: 0.10,
    Obligation.REQUIRES_THAW: 0.25,
    Obligation.REQUIRES_CONSENT: 0.25,
    Obligation.OVERWRITE_BLOCKED: 0.20,
}

# Reservoir "sensitivity" proxy in [0,1]: how privacy-laden the layer is. Derived
# from §5.3 semantics + §10 access restrictiveness (sacred/glacier highest).
_RESERVOIR_SENSITIVITY: dict[Reservoir, float] = {
    Reservoir.WORKING_STREAM: 0.10,
    Reservoir.SURFACE: 0.25,
    Reservoir.CLOUD: 0.35,
    Reservoir.GROUNDWATER: 0.70,
    Reservoir.OCEAN: 0.55,
    Reservoir.CONTAMINATED: 0.60,
    Reservoir.GLACIER: 0.90,
    Reservoir.SACRED: 0.95,
}


def _clamp(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def permission_score(droplet: Droplet, agent: AgentIdentity) -> float:
    """Score how cleanly ``agent`` may read ``droplet`` (1.0 clean .. 0.0 denied).

    Evaluated against a neutral READ context (no consent/thaw pre-granted) so the
    score reflects the *standing* access friction. Obligations and an
    above-minimum trust margin both shift the score; a denial collapses it to 0.
    """
    context = AccessContext()
    decision = check_access(droplet, agent, context, Operation.READ)
    if not decision.allowed:
        return 0.0

    score = 1.0
    for obligation in decision.obligations:
        score -= _OBLIGATION_PENALTY.get(obligation, 0.10)

    # Small reward for trust headroom above the reservoir's minimum, so a
    # high-trust agent on a low-trust reservoir never scores *below* an
    # exactly-qualified one (keeps the score monotone in trust).
    rule = rule_for(droplet.reservoir)
    required_rank = {
        AccessLevel.SESSION_AGENTS: _TRUST_RANK[TrustLevel.SESSION],
        AccessLevel.APPROVED_AGENTS: _TRUST_RANK[TrustLevel.APPROVED],
        AccessLevel.HIGH_TRUST_AGENTS_ONLY: _TRUST_RANK[TrustLevel.HIGH_TRUST],
    }.get(rule.access_level, _TRUST_RANK[TrustLevel.SESSION])
    headroom = _TRUST_RANK[agent.trust_level] - required_rank
    if headroom > 0:
        score += 0.02 * headroom

    return _clamp(score)


def reservoir_sensitivity(reservoir: Reservoir) -> float:
    """Privacy sensitivity proxy for a reservoir, in ``[0, 1]``."""
    return _RESERVOIR_SENSITIVITY.get(reservoir, 0.5)


def privacy_risk(droplet: Droplet, context: AccessContext | None = None) -> float:
    """Estimate the privacy risk of surfacing ``droplet``, in ``[0, 1]``.

    Combines three monotone proxies (the droplet schema has no first-class
    ``sensitivity`` float, so we derive one):

    * visibility — private weighs most, shared less, public least;
    * factual uncertainty — ``1 - confidence`` (charged-but-uncertain memory is
      riskier to expose; §16);
    * reservoir sensitivity — sacred/glacier/groundwater rank high.

    A ``safe_context`` recall (the caller asserting a vetted setting) damps the
    result, never raises it.
    """
    perms = droplet.permissions
    visibility_risk = {
        Visibility.PRIVATE: 1.0,
        Visibility.SHARED: 0.5,
        Visibility.PUBLIC: 0.1,
    }.get(perms.visibility, 0.5)

    uncertainty = 1.0 - _clamp(droplet.state.confidence)
    sensitivity = reservoir_sensitivity(droplet.reservoir)

    # Weighted blend; weights sum to 1.0 so the result stays in [0,1].
    risk = 0.45 * visibility_risk + 0.25 * uncertainty + 0.30 * sensitivity

    # Per-droplet escalators: requiring external-use consent or user review both
    # imply heightened sensitivity.
    if perms.requires_consent_for_external_use:
        risk = max(risk, 0.6)
    if perms.requires_user_review:
        risk = max(risk, 0.5)

    if context is not None and context.safe_context:
        risk *= 0.7

    return _clamp(risk)
