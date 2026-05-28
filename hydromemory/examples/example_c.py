"""PRD §12 Example C — a temporary task does NOT become identity.

Scenario (the §16 "over-memory reduction" guardrail):

    Raw input: the user asks about buying running shoes.
    A naive memory system over-generalizes to an identity claim
    ("User is a runner.") — which is WRONG.
    HydroMemory instead captures a single low-stakes droplet
    ("User asked about running shoes.") and makes the decision NOT to
    infiltrate it into identity-level (groundwater / sacred) memory.

What ``run`` demonstrates and returns:

A single :meth:`Engine.absorb` of the running-shoes experience — with NO
repetition signal and NO ``INFILTRATE`` call — yields a droplet that is
``phase=LIQUID``, lands in a fast/shallow reservoir (``working_stream`` or
``surface``), has a low ``state.depth``, ``cycle_count == 0``, and is NOT in the
``groundwater`` or ``sacred`` reservoirs. That is the guardrail: a transient
fact stays transient and never silently becomes an identity-level assumption.

The returned dict is the end-state facts the acceptance test asserts on.
"""
from __future__ import annotations

from typing import Any

from hydromemory.engine import Engine
from hydromemory.reservoirs import Reservoir

# The reservoirs that are *acceptable* for a transient, surface-level fact.
_SHALLOW_RESERVOIRS = {Reservoir.WORKING_STREAM.value, Reservoir.SURFACE.value}
# The reservoirs that would represent identity-level / persistent memory.
_IDENTITY_RESERVOIRS = {Reservoir.GROUNDWATER.value, Reservoir.SACRED.value}

# The transient fact we DO capture.
_RUNNING_SHOES = "User asked about running shoes."
# The identity-level over-generalization a naive system would (wrongly) store.
_BAD_IDENTITY_CLAIM = "User is a runner."


def run(engine: Engine) -> dict[str, Any]:
    """Run §12 Example C against ``engine``; return the end-state facts."""
    print("\n=== Example C — temporary task does NOT become identity ===")
    print(f"Raw input: {_RUNNING_SHOES!r}")
    print(f"Naive (WRONG) system would conclude: {_BAD_IDENTITY_CLAIM!r}")

    # One absorb of the experience: no repetition, no INFILTRATE, no identity
    # context flags. This is the full §14 capture pipeline.
    decision = engine.absorb(_RUNNING_SHOES, source="conversation")
    droplet = engine.repo.get(decision["droplet_id"])
    assert droplet is not None, "freshly absorbed droplet must be retrievable"

    phase = droplet.phase.value
    reservoir = droplet.reservoir.value
    depth = float(droplet.state.depth)
    gravity = float(droplet.state.gravity)
    cycle_count = int(droplet.cycle.cycle_count)
    triggers = list(decision.get("triggers", []))
    infiltrated = reservoir in _IDENTITY_RESERVOIRS

    print(
        f"HydroMemory decision: droplet={droplet.content!r} "
        f"phase={phase} reservoir={reservoir} depth={depth:.2f} "
        f"cycle_count={cycle_count} triggers={triggers}"
    )
    print(
        "DECISION: do NOT infiltrate into identity memory "
        f"(infiltrated={infiltrated})."
    )

    # Contrast: confirm no "User is a runner." identity droplet was created. The
    # only droplet in the store is the transient running-shoes fact.
    all_contents = [
        d.content
        for d in (engine.repo.get(i) for i in engine.repo.all_ids())
        if d is not None
    ]
    identity_droplet_created = _BAD_IDENTITY_CLAIM in all_contents
    print(
        f"Contrast: identity claim {_BAD_IDENTITY_CLAIM!r} created? "
        f"{identity_droplet_created} (stored droplets={len(all_contents)})"
    )

    return {
        "droplet_id": droplet.id,
        "content": droplet.content,
        "phase": phase,
        "reservoir": reservoir,
        "depth": depth,
        "gravity": gravity,
        "cycle_count": cycle_count,
        "triggers": triggers,
        "stored": bool(decision.get("stored", decision.get("store"))),
        # Guardrail facts.
        "infiltrated_to_identity": infiltrated,
        "is_shallow_reservoir": reservoir in _SHALLOW_RESERVOIRS,
        "identity_droplet_created": identity_droplet_created,
        "stored_droplet_count": len(all_contents),
    }
