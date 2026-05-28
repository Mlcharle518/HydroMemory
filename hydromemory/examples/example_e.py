"""PRD §12 Example E — Polluted memory becomes filtered memory.

Scenario (PRD §12 Example E; contamination §10.1, forgetting/filtering §11):

* A low-confidence, emotionally-charged inference is captured as a memory and
  marked **polluted**:  *"User hates working with teams."*  It is routed to the
  ``contaminated`` reservoir, its purity is low, and it is **not usable for
  generation** until repaired.
* The Filtration agent then **filters** it: ``polluted -> filtered``, purity is
  raised, it leaves the contaminated pool, and it becomes usable again. The
  spec's illustrative reframe is the softer, qualified reading:
  *"User may prefer autonomy and becomes frustrated by poorly structured
  collaboration."*

This module performs that transition end to end and returns the before/after
facts the acceptance test asserts on.

GAP note: the engine's ``contamination.filter_droplet`` repairs the *state*
(phase / reservoir / purity / usability) but does not rewrite the droplet's
``content`` to the §12 reframed sentence. We attach the illustrative reframe
under ``meta['reframed_content']`` so the demonstrated end-state carries it,
while the test asserts the load-bearing phase/purity/usability transition. The
engine code is deliberately left unmodified.
"""
from __future__ import annotations

from typing import Any

from hydromemory.examples._harness import banner
from hydromemory.schema import State
from hydromemory.verbs import Verbs

# The §12 Example E illustrative strings.
POLLUTED_CONTENT = "User hates working with teams."
FILTERED_CONTENT = (
    "User may prefer autonomy and becomes frustrated by poorly structured collaboration."
)
POLLUTION_REASON = "Low-confidence inference from an emotionally charged conversation."

# Deliberately low purity for the polluted droplet so the "purity RAISED by
# filtering" transition is observable (filter raises it to >= FILTERED_PURITY).
POLLUTED_PURITY = 0.25


def _usable_for_generation(droplet: Any) -> bool:
    """Whether governance would let this droplet feed generation (§10.1)."""
    return bool(droplet.meta.get("usable_for_generation", False))


def run(engine: Any) -> dict[str, Any]:
    """Run §12 Example E and return the polluted -> filtered end-state facts.

    Returns a dict with the before/after phase, reservoir, purity, and usability
    so callers (the acceptance test, the CLI demo) can verify the transition.
    """
    verbs: Verbs = engine.verbs

    banner("Example E — Polluted memory becomes filtered memory")

    # 1. Absorb the raw, low-confidence inference, then mark it polluted (§10.1).
    droplet = verbs.absorb(
        POLLUTED_CONTENT,
        source="conversation",
        context={"topic": "team collaboration", "tone": "frustrated"},
        state=State(purity=POLLUTED_PURITY, emotional_charge=0.7, confidence=0.4),
    )
    polluted = verbs.pollute(droplet, POLLUTION_REASON)

    # The contamination/forgetting verbs mutate and return the SAME droplet in
    # place (no copy), so we must snapshot the polluted facts now — before
    # filtering rewrites the same object's phase/reservoir/purity.
    polluted_purity = float(polluted.state.purity)
    before: dict[str, Any] = {
        "phase": polluted.phase.value,
        "reservoir": polluted.reservoir.value,
        "purity": polluted_purity,
        "usable_for_generation": _usable_for_generation(polluted),
        "requires_filtering": bool(polluted.meta.get("requires_filtering", False)),
    }
    reason = polluted.meta.get("reason")
    print(
        f"  polluted: phase={before['phase']} "
        f"reservoir={before['reservoir']} "
        f"purity={polluted_purity:.2f} usable={before['usable_for_generation']}"
    )
    print(f"    reason: {reason!r}")

    # 2. Filter it: the Filtration agent repairs polluted -> filtered (§11).
    filtered = verbs.filter(polluted)

    # The engine repairs state but not wording; attach the §12 illustrative
    # reframe to the demonstrated end-state (see GAP note in the module docstring).
    filtered.meta["reframed_content"] = FILTERED_CONTENT
    engine.repo.upsert(filtered)

    filtered_purity = float(filtered.state.purity)
    after: dict[str, Any] = {
        "phase": filtered.phase.value,
        "reservoir": filtered.reservoir.value,
        "purity": filtered_purity,
        "usable_for_generation": _usable_for_generation(filtered),
        "requires_filtering": bool(filtered.meta.get("requires_filtering", False)),
    }
    print(
        f"  filtered: phase={after['phase']} "
        f"reservoir={after['reservoir']} "
        f"purity={filtered_purity:.2f} usable={after['usable_for_generation']}"
    )
    print(f"    reframed_as: {FILTERED_CONTENT!r}")
    print(f"    purity raised: {polluted_purity:.2f} -> {filtered_purity:.2f}")

    return {
        "droplet_id": filtered.id,
        "polluted_content": POLLUTED_CONTENT,
        "reframed_content": FILTERED_CONTENT,
        "reason": reason,
        "before": before,
        "after": after,
        "purity_raised": filtered_purity > polluted_purity,
        "content_rewritten_by_engine": filtered.content != POLLUTED_CONTENT,
    }


__all__ = [
    "run",
    "POLLUTED_CONTENT",
    "FILTERED_CONTENT",
    "POLLUTION_REASON",
    "POLLUTED_PURITY",
]
