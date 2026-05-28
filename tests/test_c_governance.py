"""Track C governance tests (PRD §10): policy fidelity, the ``check_access``
truth-table over reservoir × operation × trust × consent/thaw, and the
monotonicity of ``permission_score`` / ``privacy_risk``.
"""
from __future__ import annotations

import pytest

from hydromemory.governance import (
    AccessContext,
    AccessLevel,
    AgentIdentity,
    Obligation,
    Operation,
    TrustLevel,
    all_rules,
    check_access,
    permission_score,
    privacy_risk,
    rule_for,
)
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Permissions, State, Visibility


def make_droplet(
    reservoir: Reservoir,
    *,
    visibility: Visibility = Visibility.PRIVATE,
    allowed_agents: list[str] | None = None,
    confidence: float = 0.9,
    external_sharing: bool = False,
    requires_consent_for_external_use: bool = False,
    requires_user_review: bool = False,
    usable_for_generation: bool | None = None,
) -> Droplet:
    meta: dict = {}
    if usable_for_generation is not None:
        meta["usable_for_generation"] = usable_for_generation
    return Droplet(
        id="mem_test",
        content="x",
        reservoir=reservoir,
        state=State(confidence=confidence),
        permissions=Permissions(
            owner="user",
            visibility=visibility,
            allowed_agents=allowed_agents or [],
            external_sharing=external_sharing,
            requires_consent_for_external_use=requires_consent_for_external_use,
            requires_user_review=requires_user_review,
        ),
        meta=meta,
    )


SESSION = AgentIdentity("a", TrustLevel.SESSION)
APPROVED = AgentIdentity("a", TrustLevel.APPROVED)
HIGH = AgentIdentity("a", TrustLevel.HIGH_TRUST)
FILTRATION = AgentIdentity("filtration", TrustLevel.HIGH_TRUST, is_filtration=True)
USER_PROXY = AgentIdentity("privacy", TrustLevel.HIGH_TRUST, is_user_proxy=True)


# --- §10 policy fidelity ----------------------------------------------------


def test_policy_reproduces_section_10_verbatim():
    rules = all_rules()

    ws = rules[Reservoir.WORKING_STREAM]
    assert ws.access_level is AccessLevel.SESSION_AGENTS
    assert ws.user_visible is False

    surface = rules[Reservoir.SURFACE]
    assert surface.access_level is AccessLevel.APPROVED_AGENTS
    assert surface.user_visible is True

    gw = rules[Reservoir.GROUNDWATER]
    assert gw.access_level is AccessLevel.HIGH_TRUST_AGENTS_ONLY
    assert gw.requires_explanation is True

    glacier = rules[Reservoir.GLACIER]
    assert glacier.access_level is AccessLevel.EXPLICIT_USER_CONSENT
    assert glacier.requires_thaw_protocol is True

    contaminated = rules[Reservoir.CONTAMINATED]
    assert contaminated.access_level is AccessLevel.FILTRATION_AGENT_ONLY
    assert contaminated.usable_for_response is False

    sacred = rules[Reservoir.SACRED]
    assert (
        sacred.access_level
        is AccessLevel.EXPLICIT_USER_CONSENT_OR_USER_DEFINED_CORE_BEHAVIOR
    )
    assert sacred.overwrite_allowed is False


def test_policy_defaults_for_unspecified_flags():
    # surface specifies only user_visible; the rest take documented defaults.
    surface = rule_for(Reservoir.SURFACE)
    assert surface.requires_explanation is False
    assert surface.requires_thaw_protocol is False
    assert surface.usable_for_response is True
    assert surface.overwrite_allowed is True
    # cloud/ocean (not in the §10 block) get sensible documented defaults.
    assert rule_for(Reservoir.CLOUD).access_level is AccessLevel.APPROVED_AGENTS
    assert rule_for(Reservoir.OCEAN).access_level is AccessLevel.HIGH_TRUST_AGENTS_ONLY


# --- working_stream: session agents may read ---------------------------------


def test_working_stream_session_read_clean():
    d = make_droplet(Reservoir.WORKING_STREAM)
    dec = check_access(d, SESSION, AccessContext(), Operation.READ)
    assert dec.allowed is True
    assert dec.obligations == []


def test_working_stream_not_user_visible():
    d = make_droplet(Reservoir.WORKING_STREAM)
    dec = check_access(d, USER_PROXY, AccessContext(), Operation.EXPOSE_TO_USER)
    assert dec.allowed is False
    assert "user-visible" in (dec.denial_reason or "")


# --- surface: approved agents, user-visible ----------------------------------


def test_surface_requires_at_least_approved():
    d = make_droplet(Reservoir.SURFACE)
    assert check_access(d, SESSION, AccessContext(), Operation.READ).allowed is False
    assert check_access(d, APPROVED, AccessContext(), Operation.READ).allowed is True


def test_surface_expose_to_user_private_only_owner():
    # surface is user_visible, but a private droplet may only be exposed to owner.
    d = make_droplet(Reservoir.SURFACE, visibility=Visibility.PRIVATE)
    assert check_access(d, APPROVED, AccessContext(), Operation.EXPOSE_TO_USER).allowed is False
    assert check_access(d, USER_PROXY, AccessContext(), Operation.EXPOSE_TO_USER).allowed is True
    # a shared droplet on surface may be exposed by a non-owner.
    shared = make_droplet(Reservoir.SURFACE, visibility=Visibility.SHARED)
    assert check_access(shared, APPROVED, AccessContext(), Operation.EXPOSE_TO_USER).allowed is True


# --- groundwater: high trust + explanation -----------------------------------


def test_groundwater_requires_high_trust():
    d = make_droplet(Reservoir.GROUNDWATER)
    assert check_access(d, APPROVED, AccessContext(), Operation.READ).allowed is False
    dec = check_access(d, HIGH, AccessContext(), Operation.READ)
    assert dec.allowed is True


def test_groundwater_read_requires_explanation_obligation():
    d = make_droplet(Reservoir.GROUNDWATER)
    dec = check_access(d, HIGH, AccessContext(), Operation.READ)
    assert Obligation.REQUIRES_EXPLANATION in dec.obligations


# --- glacier: thaw + consent -------------------------------------------------


def test_glacier_denied_without_thaw():
    d = make_droplet(Reservoir.GLACIER)
    dec = check_access(d, HIGH, AccessContext(thaw_granted=False, consent_granted=True), Operation.READ)
    assert dec.allowed is False
    assert Obligation.REQUIRES_THAW in dec.obligations


def test_glacier_denied_without_consent():
    d = make_droplet(Reservoir.GLACIER)
    dec = check_access(d, HIGH, AccessContext(thaw_granted=True, consent_granted=False), Operation.READ)
    assert dec.allowed is False
    assert Obligation.REQUIRES_CONSENT in dec.obligations


def test_glacier_allowed_with_thaw_and_consent():
    d = make_droplet(Reservoir.GLACIER)
    dec = check_access(d, HIGH, AccessContext(thaw_granted=True, consent_granted=True), Operation.READ)
    assert dec.allowed is True
    assert Obligation.REQUIRES_THAW in dec.obligations


# --- contaminated: filtration only, never usable -----------------------------


def test_contaminated_filtration_only():
    d = make_droplet(Reservoir.CONTAMINATED)
    for agent in (SESSION, APPROVED, HIGH, USER_PROXY):
        dec = check_access(d, agent, AccessContext(), Operation.READ)
        assert dec.allowed is False
        assert dec.usable_for_generation is False
    dec = check_access(d, FILTRATION, AccessContext(), Operation.READ)
    assert dec.allowed is True
    assert dec.usable_for_generation is False


def test_contaminated_use_for_generation_denied_even_for_filtration():
    d = make_droplet(Reservoir.CONTAMINATED)
    dec = check_access(d, FILTRATION, AccessContext(), Operation.USE_FOR_GENERATION)
    assert dec.allowed is False
    assert dec.usable_for_generation is False


# --- sacred: consent + overwrite blocked -------------------------------------


def test_sacred_read_requires_consent_unless_user_proxy():
    d = make_droplet(Reservoir.SACRED)
    denied = check_access(d, HIGH, AccessContext(consent_granted=False), Operation.READ)
    assert denied.allowed is False
    assert Obligation.REQUIRES_CONSENT in denied.obligations
    assert check_access(d, HIGH, AccessContext(consent_granted=True), Operation.READ).allowed is True
    assert check_access(d, USER_PROXY, AccessContext(), Operation.READ).allowed is True


def test_sacred_overwrite_blocked():
    d = make_droplet(Reservoir.SACRED)
    dec = check_access(d, USER_PROXY, AccessContext(consent_granted=True), Operation.OVERWRITE)
    assert dec.allowed is False
    assert Obligation.OVERWRITE_BLOCKED in dec.obligations


def test_sacred_mutate_surfaces_overwrite_blocked_but_not_denied():
    # MUTATE/TRANSFORM on sacred carry the OVERWRITE_BLOCKED obligation but are
    # not hard-denied (only OVERWRITE is denied outright).
    d = make_droplet(Reservoir.SACRED)
    dec = check_access(d, USER_PROXY, AccessContext(consent_granted=True), Operation.MUTATE)
    assert dec.allowed is True
    assert Obligation.OVERWRITE_BLOCKED in dec.obligations


# --- per-droplet permissions intersect ---------------------------------------


def test_allowed_agents_restriction():
    d = make_droplet(Reservoir.SURFACE, allowed_agents=["other_agent"])
    assert check_access(d, APPROVED, AccessContext(), Operation.READ).allowed is False
    # user proxy bypasses the per-droplet allow-list.
    assert check_access(d, USER_PROXY, AccessContext(), Operation.READ).allowed is True
    # an agent named in the list is admitted.
    named = AgentIdentity("other_agent", TrustLevel.APPROVED)
    assert check_access(d, named, AccessContext(), Operation.READ).allowed is True


def test_empty_allowed_agents_means_no_restriction():
    d = make_droplet(Reservoir.SURFACE, allowed_agents=[])
    assert check_access(d, APPROVED, AccessContext(), Operation.READ).allowed is True


def test_meta_usable_for_generation_flag_blocks_generation():
    # A droplet flagged unusable in meta (e.g. by contamination) cannot be used
    # for generation even from a usable reservoir.
    d = make_droplet(Reservoir.SURFACE, usable_for_generation=False)
    dec = check_access(d, APPROVED, AccessContext(), Operation.USE_FOR_GENERATION)
    assert dec.allowed is False
    assert dec.usable_for_generation is False


# --- permission_score monotonicity -------------------------------------------


def test_permission_score_clean_allow_is_one():
    d = make_droplet(Reservoir.WORKING_STREAM)
    assert permission_score(d, SESSION) == pytest.approx(1.0)


def test_permission_score_zero_when_denied():
    d = make_droplet(Reservoir.GROUNDWATER)
    assert permission_score(d, SESSION) == 0.0  # session can't read groundwater


def test_permission_score_obligations_lower_it():
    d = make_droplet(Reservoir.GROUNDWATER)
    score = permission_score(d, HIGH)  # allowed but REQUIRES_EXPLANATION
    assert 0.0 < score < 1.0


def test_permission_score_monotone_in_trust():
    # On groundwater: session denied (0) <= approved denied (0) < high allowed.
    d = make_droplet(Reservoir.GROUNDWATER)
    s_session = permission_score(d, SESSION)
    s_high = permission_score(d, HIGH)
    assert s_session <= s_high
    # On working_stream a higher-trust agent never scores below a session one.
    ws = make_droplet(Reservoir.WORKING_STREAM)
    assert permission_score(ws, HIGH) >= permission_score(ws, SESSION)


# --- privacy_risk monotonicity -----------------------------------------------


def test_privacy_risk_private_higher_than_public():
    priv = make_droplet(Reservoir.SURFACE, visibility=Visibility.PRIVATE)
    pub = make_droplet(Reservoir.SURFACE, visibility=Visibility.PUBLIC)
    assert privacy_risk(priv) > privacy_risk(pub)


def test_privacy_risk_rises_with_uncertainty():
    confident = make_droplet(Reservoir.SURFACE, confidence=0.95)
    uncertain = make_droplet(Reservoir.SURFACE, confidence=0.1)
    assert privacy_risk(uncertain) > privacy_risk(confident)


def test_privacy_risk_rises_with_reservoir_sensitivity():
    low = make_droplet(Reservoir.WORKING_STREAM)
    high = make_droplet(Reservoir.SACRED)
    assert privacy_risk(high) > privacy_risk(low)


def test_privacy_risk_in_unit_range_and_safe_context_damps():
    d = make_droplet(Reservoir.SACRED, confidence=0.1)
    base = privacy_risk(d)
    assert 0.0 <= base <= 1.0
    damped = privacy_risk(d, AccessContext(safe_context=True))
    assert damped <= base


def test_privacy_risk_consent_flag_escalates():
    plain = make_droplet(Reservoir.WORKING_STREAM, confidence=0.99, visibility=Visibility.PUBLIC)
    flagged = make_droplet(
        Reservoir.WORKING_STREAM,
        confidence=0.99,
        visibility=Visibility.PUBLIC,
        requires_consent_for_external_use=True,
    )
    assert privacy_risk(flagged) > privacy_risk(plain)
