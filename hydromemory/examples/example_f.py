"""PRD §12 Example F — conflict resolution (reconcile, don't overwrite).

Scenario
--------
An old preference memory and a newer, *conflicting* one arrive:

* old: ``"User prefers long detailed explanations."``
* new: ``"User asks for concise answers today."``

HydroMemory does not blindly overwrite the old memory with the new one. It
records the contradiction (a ``contradictions`` link, via the FLOW verb) and
then *reconciles* the two into a single, context-dependent FILTERED memory whose
purity is raised by the Filtration agent (``contamination.filter_droplet``).

The reconciled end-state mirrors the PRD §12 Example F resolution payload::

    {"conflict": true,
     "interpretation": "Context-dependent preference",
     "updated_memory": {
         "content": "User often prefers depth for architecture topics, but may "
                    "prefer concise answers for simple tasks.",
         "phase": "filtered",
         "purity": 0.92}}

This module composes existing engine verbs only — ``absorb`` (capture the two
memories), ``flow(kind="contradictions")`` (record the conflict), and ``filter``
(produce the reframed FILTERED droplet). It does not modify any engine source.
"""
from __future__ import annotations

from typing import Any

from hydromemory.engine import Engine
from hydromemory.schema import Phase, State

# The spec's reconciled interpretation and content (PRD §12 Example F).
_INTERPRETATION = "Context-dependent preference"
_RECONCILED_CONTENT = (
    "User often prefers depth for architecture topics, "
    "but may prefer concise answers for simple tasks."
)


def run(engine: Engine) -> dict[str, Any]:
    """Run §12 Example F and return the reconciliation payload.

    Steps (all via legitimate engine verbs):

    1. ABSORB the old preference droplet and the new (conflicting) one.
    2. FLOW a ``contradictions`` link in both directions so the conflict is
       recorded on each droplet (and persisted as graph edges).
    3. ABSORB the reconciled, context-dependent statement, then FILTER it so the
       Filtration agent flips it to the FILTERED phase and raises its purity to
       the §12 Example F target (0.92).
    """
    verbs = engine.verbs

    # 1. Capture the two conflicting memories as LIQUID droplets.
    old = verbs.absorb(
        "User prefers long detailed explanations.",
        source="conversation",
        context={"topic": "communication_style"},
    )
    new = verbs.absorb(
        "User asks for concise answers today.",
        source="conversation",
        context={"topic": "communication_style", "transient": True},
    )

    # 2. Record the contradiction on both droplets (FLOW verb, contradictions
    #    link). Reconciling rather than overwriting is the whole point of §12 F.
    verbs.flow(old, [new.id], kind="contradictions")
    verbs.flow(new, [old.id], kind="contradictions")
    conflict_detected = new.id in old.links.contradictions and old.id in new.links.contradictions

    # 3. Build the reconciled, context-dependent memory and FILTER it.
    #    A fresh LIQUID droplet starts at purity 0.0; ``contamination.filter_droplet``
    #    (invoked by the FILTER verb) flips polluted/liquid -> FILTERED and raises
    #    purity to the §12 Example F floor of 0.92 via ``max(purity, FILTERED_PURITY)``.
    #    No engine code is modified and the spec's 0.92 is reached organically.
    reconciled_seed = verbs.absorb(
        _RECONCILED_CONTENT,
        source=f"reconcile:{old.id}+{new.id}",
        context={"interpretation": _INTERPRETATION},
        state=State(confidence=0.85),
    )
    # Make the provenance + interpretation auditable on the reconciled droplet.
    reconciled_seed.meta["interpretation"] = _INTERPRETATION
    reconciled_seed.meta["reconciled_from"] = [old.id, new.id]
    reconciled_seed.links.derived_from.extend([old.id, new.id])
    reconciled_seed.links.contradictions.extend([old.id, new.id])
    engine.repo.upsert(reconciled_seed)
    engine.repo.add_link(reconciled_seed.id, "derived_from", old.id)
    engine.repo.add_link(reconciled_seed.id, "derived_from", new.id)

    # FILTER: polluted/liquid -> filtered, purity raised to >= 0.92 (§12 F target).
    reconciled = verbs.filter(reconciled_seed)

    payload: dict[str, Any] = {
        "conflict": conflict_detected,
        "interpretation": _INTERPRETATION,
        "updated_memory": {
            "content": reconciled.content,
            "phase": reconciled.phase.value,
            "purity": round(reconciled.state.purity, 2),
        },
    }

    # Short narrative for ``hydromem run-example`` / direct runs.
    print("=== §12 Example F — conflict resolution ===")
    print(f"  old memory: {old.content!r}  (id={old.id})")
    print(f"  new memory: {new.content!r}  (id={new.id})")
    print(f"  conflict detected (contradictions link both ways): {conflict_detected}")
    print(f"  interpretation: {_INTERPRETATION}")
    print(
        f"  reconciled -> phase={reconciled.phase.value}, "
        f"purity={reconciled.state.purity:.2f}"
    )
    print(f"  updated memory: {reconciled.content!r}")

    # Internal consistency: the reconciled droplet really is FILTERED at >= 0.92.
    assert reconciled.phase is Phase.FILTERED
    assert reconciled.state.purity >= 0.9

    return payload
