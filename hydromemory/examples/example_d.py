"""PRD §12 Example D — Sensitive memory gets frozen.

Scenario (PRD §12 Example D):

    Raw input: user shares a traumatic event.
    HydroMemory response: phase=ice; reservoir=glacier; access=restricted;
    transformation disabled without user consent; recall only in safe, relevant
    contexts.

This module demonstrates that end-state against the real engine:

1.  ABSORB the sensitive memory (lands LIQUID in the working stream).
2.  FREEZE it (``verbs.freeze``) -> phase ``ICE``, reservoir ``GLACIER`` — a
    preserved, high-integrity snapshot.
3.  Exercise §10 governance on the frozen droplet:
      * ``Operation.TRANSFORM`` *without* consent/thaw is DENIED and carries the
        ``REQUIRES_THAW`` / ``REQUIRES_CONSENT`` obligations (transformation is
        disabled without user consent);
      * the same transform *with* ``consent_granted=True`` and
        ``thaw_granted=True`` is PERMITTED.
4.  Show recall depends on a safe context: a frozen glacier droplet does not
    surface through the ordinary recall path, and ``verbs.melt`` only reactivates
    it when the context is safe (``MELT`` is blocked otherwise).

``run`` prints a short narrative and returns the end-state facts (including the
access-decision results) so the acceptance test can assert them.
"""
from __future__ import annotations

from typing import Any

from hydromemory.governance import (
    AccessContext,
    AgentIdentity,
    Operation,
    TrustLevel,
)
from hydromemory.governance import (
    check_access as check_access_fn,
)
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase

# The traumatic disclosure the user shares (contains the sensitive marker the
# stub classifier keys on, so the droplet is flagged as private + sensitive).
SENSITIVE_INPUT = (
    "I want to tell you about a traumatic event from my past that still hurts; "
    "I felt afraid and ashamed and I have never told anyone."
)


def _decision_facts(decision: Any) -> dict[str, Any]:
    """Flatten an :class:`AccessDecision` into a plain, assertable dict."""
    return {
        "allowed": bool(decision.allowed),
        "denial_reason": decision.denial_reason,
        "obligations": [o.value for o in decision.obligations],
        "usable_for_generation": bool(decision.usable_for_generation),
    }


def run(engine: Any) -> dict[str, Any]:
    """Run §12 Example D end-to-end and return the end-state facts."""
    print("\n=== §12 Example D — Sensitive memory gets frozen ===")

    # The agent that will later try to transform the frozen memory. An approved
    # agent clears the glacier trust floor, so the only gate left is thaw/consent.
    agent = AgentIdentity(name="reasoning_agent", trust_level=TrustLevel.APPROVED)

    # 1. ABSORB — the user shares a traumatic event.
    absorb = engine.absorb(
        SENSITIVE_INPUT,
        source="conversation",
        context={"topic": "personal trauma", "session_type": "support"},
        agent=AgentIdentity(name="capture_agent", trust_level=TrustLevel.SESSION),
    )
    droplet_id = str(absorb["droplet_id"])
    print(
        f"  absorbed sensitive memory {droplet_id}: "
        f"phase={absorb['phase']} reservoir={absorb['reservoir']} stored={absorb['stored']}"
    )

    droplet = engine.repo.get(droplet_id)
    assert isinstance(droplet, Droplet)

    # 2. FREEZE — preserve it as a high-integrity ICE snapshot in the glacier.
    #    Freeze runs an OVERWRITE policy review; the droplet is still in the
    #    working stream at this point, so the review allows the snapshot.
    frozen = engine.verbs.freeze(
        droplet,
        agent=agent,
        context=AccessContext(consent_granted=True, thaw_granted=True),
    )
    print(
        f"  froze memory -> phase={frozen.phase.value} reservoir={frozen.reservoir.value} "
        f"(integrity={frozen.state.integrity:.2f})"
    )

    # 3. GOVERNANCE — transformation is disabled without user consent/thaw.
    denied = check_access_fn(
        frozen,
        agent,
        AccessContext(consent_granted=False, thaw_granted=False),
        Operation.TRANSFORM,
    )
    print(
        f"  TRANSFORM without consent/thaw -> allowed={denied.allowed} "
        f"obligations={[o.value for o in denied.obligations]} reason={denied.denial_reason!r}"
    )

    permitted = check_access_fn(
        frozen,
        agent,
        AccessContext(consent_granted=True, thaw_granted=True),
        Operation.TRANSFORM,
    )
    print(
        f"  TRANSFORM with consent+thaw   -> allowed={permitted.allowed} "
        f"obligations={[o.value for o in permitted.obligations]}"
    )

    # 4. RECALL only in safe, relevant contexts. A frozen glacier droplet sits
    #    behind a high recall threshold, so it does not surface through the
    #    ordinary recall path regardless of the surrounding context.
    unsafe_results = engine.recall(
        "traumatic event from the past",
        agent=agent,
        context=AccessContext(safe_context=False, consent_granted=False, thaw_granted=False),
    )
    safe_results = engine.recall(
        "traumatic event from the past",
        agent=agent,
        context=AccessContext(safe_context=True, consent_granted=True, thaw_granted=True),
    )
    unsafe_ids = [r.droplet_id for r in unsafe_results]
    safe_ids = [r.droplet_id for r in safe_results]
    recalled_unsafe = droplet_id in unsafe_ids
    recalled_safe = droplet_id in safe_ids
    print(
        f"  recall (frozen surfaces?): unsafe_context={recalled_unsafe} "
        f"safe_context={recalled_safe}"
    )

    # Reactivation (MELT) is the gate that *does* turn on safe context: it is
    # blocked in an unsafe context and only thaws ICE->LIQUID when context is safe.
    blocked = engine.repo.get(droplet_id)
    assert isinstance(blocked, Droplet)
    melt_blocked = engine.verbs.melt(blocked, context={"safe_context": False})
    melt_blocked_phase = melt_blocked.phase.value
    melt_blocked_reason = melt_blocked.meta.get("melt_blocked")
    print(
        f"  MELT in unsafe context -> phase={melt_blocked_phase} "
        f"blocked_reason={melt_blocked_reason!r}"
    )

    thawing = engine.repo.get(droplet_id)
    assert isinstance(thawing, Droplet)
    melted = engine.verbs.melt(thawing, context={"safe_context": True})
    print(
        f"  MELT in safe context   -> phase={melted.phase.value} "
        f"reservoir={melted.reservoir.value}"
    )

    return {
        "droplet_id": droplet_id,
        # Absorbed (pre-freeze) facts.
        "absorbed_stored": bool(absorb["stored"]),
        "absorbed_phase": str(absorb["phase"]),
        # Frozen end-state.
        "phase": frozen.phase.value,
        "reservoir": frozen.reservoir.value,
        "is_ice": frozen.phase is Phase.ICE,
        "is_glacier": frozen.reservoir is Reservoir.GLACIER,
        # Governance decisions.
        "transform_without_consent": _decision_facts(denied),
        "transform_with_consent": _decision_facts(permitted),
        # Recall behaviour (frozen memory does not surface in ordinary recall).
        "recalled_in_unsafe_context": recalled_unsafe,
        "recalled_in_safe_context": recalled_safe,
        # MELT: blocked without a safe context, reactivates with one.
        "melt_blocked_phase": melt_blocked_phase,
        "melt_blocked_reason": melt_blocked_reason,
        "melted_phase": melted.phase.value,
        "melted_reservoir": melted.reservoir.value,
    }
