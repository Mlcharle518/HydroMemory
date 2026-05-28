"""PRD §12 Example B — *Preference becomes principle*.

The inverse of Example C's over-memory guard: here a **repeated** preference is
*supposed* to reach identity-level groundwater.

Narrative (PRD §12 Example B):

* Raw experiences (repeated): the user keeps asking for systems maps, rejects
  shallow summaries, and prefers architecture / abstraction / implementation.
* EVAPORATION distils each into the pattern "user values structural
  intelligence".
* CONDENSATION clusters those patterns into a CLOUD describing a *cognitive
  style*: systems thinking, abstraction, implementation, disdain for shallow
  framing.
* INFILTRATION sinks the consolidated principle into GROUNDWATER as a durable
  rule: "When helping this user, prioritize deep architecture, mechanisms, and
  executable frameworks."
* Resulting agent behaviour: give maps, protocols, engines, interfaces, and
  implementation logic; avoid generic explanations.

``run`` performs the scenario on a supplied engine and returns the end-state
facts the acceptance test asserts:

* a durable principle droplet lands in **phase GROUNDWATER / reservoir
  GROUNDWATER** holding the depth/architecture principle;
* a recall in an *architecture-help* context (by a trusted agent, since §10
  gates groundwater to high-trust agents) returns **behavioural** guidance that
  reflects the depth preference without quoting the memory.
"""
from __future__ import annotations

from typing import Any

from hydromemory.engine import Engine
from hydromemory.examples._harness import banner
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.recall import RecallMode
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Phase, State

# The repeated raw experiences (the user keeps signalling the same preference).
RAW_EXPERIENCES: tuple[str, ...] = (
    "User asks for a systems map of the whole architecture again.",
    "User rejects the shallow summary and wants the underlying mechanism.",
    "User prefers architecture, abstraction, and concrete implementation.",
    "User asks again for the systems map, not a high-level overview.",
    "User rejects a generic explanation and asks for the executable framework.",
)

# The consolidated, identity-level principle that should settle in groundwater.
PRINCIPLE_TEXT = (
    "When helping this user, prioritize deep architecture, mechanisms, and "
    "executable frameworks over generic explanations."
)

# The cognitive-style cloud theme (PRD §12 Example B).
COGNITIVE_STYLE_THEME = (
    "cognitive style: systems thinking, abstraction, implementation, "
    "disdain for shallow framing"
)

ARCHITECTURE_CTX: dict[str, Any] = {"topic": "architecture", "session_type": "design"}


def run(engine: Engine) -> dict[str, Any]:
    """Drive PRD §12 Example B and return its end-state facts.

    Steps (printed as a short narrative):
      1. ABSORB the repeated preference experiences; IRRIGATE each so the
         repetition is recorded on the cycle counter.
      2. EVAPORATE each raw experience into a structural-intelligence pattern.
      3. CONDENSE the patterns into a CLOUD describing the cognitive style.
      4. ABSORB the consolidated principle (seeded with a settled, high-purity,
         identity-relevant state) and INFILTRATE it into GROUNDWATER; link the
         cloud as its derived source.
      5. RECALL in an architecture-help context (high-trust agent) -> behavioural
         guidance reflecting the depth preference.
    """
    verbs = engine.verbs
    banner("Example B — preference becomes principle")

    # 1. Repeated raw experiences. ------------------------------------------
    raw = [
        verbs.absorb(text, source="conversation", context=dict(ARCHITECTURE_CTX))
        for text in RAW_EXPERIENCES
    ]
    # Record the repetition on each droplet's cycle counter (IRRIGATE = apply a
    # pattern to a new task / touch the cycle). Repetition is what *earns* the
    # path to identity-level groundwater in this example.
    for drop in raw:
        verbs.irrigate(drop, task="architecture request")
        verbs.irrigate(drop, task="architecture request (repeat)")
    print(
        f"  ABSORB x{len(raw)}: captured repeated structural-intelligence "
        f"preferences (max cycle_count={max(d.cycle.cycle_count for d in raw)})."
    )

    # 2. Evaporate each into a pattern. -------------------------------------
    vapors = [verbs.evaporate(drop) for drop in raw]
    print(
        f"  EVAPORATE x{len(vapors)}: abstracted to structural patterns, e.g. "
        f"{vapors[0].content!r}."
    )

    # 3. Condense the patterns into a cognitive-style cloud. ----------------
    cloud = verbs.condense(vapors, theme=COGNITIVE_STYLE_THEME)
    print(
        f"  CONDENSE: clustered {len(vapors)} patterns into CLOUD "
        f"{cloud.id} ({cloud.phase.value}/{cloud.reservoir.value})."
    )

    # 4. Consolidate into a durable groundwater principle. ------------------
    # The principle is a *settled, consolidated* abstraction (high confidence
    # and purity, identity-relevant gravity) -- not a raw, noisy inference -- so
    # we seed its state accordingly before sinking it into deep storage.
    consolidated_state = State(
        confidence=0.9,
        purity=0.95,
        gravity=0.6,
        pressure=0.4,
        integrity=0.7,
    )
    principle = verbs.absorb(
        PRINCIPLE_TEXT,
        source="condense",
        context=dict(ARCHITECTURE_CTX),
        state=consolidated_state,
    )
    groundwater = verbs.infiltrate(principle, context=dict(ARCHITECTURE_CTX))
    # Trace the principle back to the cognitive-style cloud it came from.
    verbs.flow(groundwater, [cloud.id], kind="derived_from")
    print(
        f"  INFILTRATE: principle {groundwater.id} settled into "
        f"{groundwater.phase.value}/{groundwater.reservoir.value} "
        f"(depth={groundwater.state.depth:.2f}, gravity={groundwater.state.gravity:.2f})."
    )

    # 5. Recall in an architecture-help context. ----------------------------
    # §10 gates groundwater (identity-level memory) to high-trust agents, so the
    # behavioural guidance surfaces for a trusted assistant.
    agent = AgentIdentity(name="assistant", trust_level=TrustLevel.HIGH_TRUST)
    results = engine.recall(
        "How should you help me design this architecture?",
        agent=agent,
        context=dict(ARCHITECTURE_CTX),
    )
    principle_hits = [r for r in results if r.droplet_id == groundwater.id]
    top = principle_hits[0] if principle_hits else None
    if top is not None:
        print(
            f"  RECALL (architecture help): {top.mode.value} guidance -> "
            f"{top.internal_guidance}"
        )
    else:
        print("  RECALL (architecture help): principle did not surface.")

    # Groundwater reservoir contents (the durable identity-level layer).
    groundwater_droplets = engine.repo.query(reservoir=Reservoir.GROUNDWATER)

    return {
        "raw_count": len(raw),
        "max_cycle_count": max(d.cycle.cycle_count for d in raw),
        "vapor_count": len(vapors),
        "cloud_id": cloud.id,
        "cloud_phase": cloud.phase.value,
        "cloud_theme": cloud.content,
        "principle_id": groundwater.id,
        "principle_text": groundwater.content,
        "principle_phase": groundwater.phase.value,
        "principle_reservoir": groundwater.reservoir.value,
        "principle_depth": groundwater.state.depth,
        "principle_gravity": groundwater.state.gravity,
        "principle_derived_from_cloud": cloud.id in groundwater.links.derived_from,
        "reached_groundwater": (
            groundwater.phase is Phase.GROUNDWATER
            and groundwater.reservoir is Reservoir.GROUNDWATER
        ),
        "groundwater_count": len(groundwater_droplets),
        "recall_count": len(results),
        "principle_recalled": top is not None,
        "recall_mode": top.mode.value if top is not None else None,
        "recall_is_behavioral": (top is not None and top.mode is RecallMode.BEHAVIORAL),
        "recall_score": top.score if top is not None else 0.0,
        "recall_guidance": top.internal_guidance if top is not None else "",
        "recall_quotes_memory": bool(top.show_to_user) if top is not None else False,
    }


__all__ = ["run", "PRINCIPLE_TEXT", "RAW_EXPERIENCES"]
