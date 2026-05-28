"""PRD §12 Example A -- "Meeting dismissal to cloud recall".

The hydraulic lifecycle this scenario demonstrates:

1.  ABSORB a fresh, emotionally charged LIQUID droplet:
    "I was dismissed during a meeting." (tags: work, authority, public speaking).
2.  EVAPORATE it into an abstracted VAPOR pattern droplet ("being ignored in
    public") linked ``derived_from`` the original liquid memory.
3.  ABSORB the other related dismissal experiences (interrupted by a friend,
    ignored by a parent, talked over in a group) and EVAPORATE each into its own
    VAPOR pattern.
4.  CONDENSE the vapor patterns into a single CLOUD droplet themed
    "social invisibility" (``derived_from`` each vapor).
5.  PRECIPITATE / recall the pattern when a new trigger appears ("Let's move
    on."): the recall surfaces the invisibility/erasure pattern.

``run(engine)`` performs the lifecycle (printing a short narrative of each step)
and returns a dict of end-state facts the acceptance test asserts.
"""
from __future__ import annotations

from typing import Any

from hydromemory.recall import RecallResult
from hydromemory.schema import Droplet

# The initial experience (PRD §12 Example A loose-shape droplet).
INITIAL_CONTENT = "I was dismissed during a meeting."
INITIAL_CONTEXT: dict[str, Any] = {
    "topic": "social invisibility",
    "tags": ["work", "authority", "public speaking", "social invisibility"],
    "emotional_charge": 0.68,
    "pressure": 0.55,
}

# The other related dismissal experiences that form the cloud cluster.
RELATED_EXPERIENCES: tuple[str, ...] = (
    "A friend interrupted me while I was talking.",
    "My parent ignored me when I spoke.",
    "They talked over me in the group.",
)

# The condensed theme for the cluster.
CLOUD_THEME = "social invisibility"

# The later precipitation trigger + recall query ("This feels like being erased.").
RECALL_QUERY = "being ignored in public, interrupted and talked over, feeling erased"
RECALL_CONTEXT: dict[str, Any] = {
    "topic": "social invisibility",
    "tags": ["social invisibility", "public speaking"],
}


def run(engine: object) -> dict[str, Any]:
    """Run §12 Example A end-to-end against ``engine`` and return end-state facts."""
    verbs = engine.verbs  # type: ignore[attr-defined]

    print("\n=== §12 Example A: Meeting dismissal -> cloud recall ===")

    # 1. ABSORB the initial liquid experience.
    decision = engine.absorb(  # type: ignore[attr-defined]
        INITIAL_CONTENT,
        source="experience",
        context=INITIAL_CONTEXT,
    )
    original_id = decision["droplet_id"]
    original: Droplet | None = engine.repo.get(original_id)  # type: ignore[attr-defined]
    assert original is not None
    print(
        f"1. ABSORB liquid droplet {original_id} "
        f"(phase={original.phase.value}): {original.content!r}"
    )

    # 2. EVAPORATE the original into a VAPOR pattern droplet.
    primary_vapor = verbs.evaporate(original)
    print(
        f"2. EVAPORATE -> vapor droplet {primary_vapor.id} "
        f"(phase={primary_vapor.phase.value}): {primary_vapor.content!r} "
        f"[derived_from={primary_vapor.links.derived_from}]"
    )

    # 3. ABSORB + EVAPORATE the related dismissal experiences.
    vapors: list[Droplet] = [primary_vapor]
    source_ids: list[str] = [original_id]
    for content in RELATED_EXPERIENCES:
        rel_decision = engine.absorb(  # type: ignore[attr-defined]
            content,
            source="experience",
            context={"topic": CLOUD_THEME, "tags": [CLOUD_THEME]},
        )
        rel = engine.repo.get(rel_decision["droplet_id"])  # type: ignore[attr-defined]
        assert rel is not None
        source_ids.append(rel.id)
        vapor = verbs.evaporate(rel)
        vapors.append(vapor)
        print(
            f"   ABSORB+EVAPORATE related: {content!r} "
            f"-> vapor {vapor.id} ({vapor.phase.value}): {vapor.content!r}"
        )

    # 4. CONDENSE the vapor patterns into a CLOUD droplet themed "social invisibility".
    cloud = verbs.condense(vapors, theme=CLOUD_THEME)
    print(
        f"4. CONDENSE {len(vapors)} vapors -> cloud droplet {cloud.id} "
        f"(phase={cloud.phase.value}, reservoir={cloud.reservoir.value}): "
        f"{cloud.content!r} [members={cloud.meta.get('members')}]"
    )

    # 5. PRECIPITATE / recall the pattern when the new trigger appears.
    results: list[RecallResult] = engine.recall(  # type: ignore[attr-defined]
        RECALL_QUERY,
        context=RECALL_CONTEXT,
    )
    print(
        f"5. RECALL (precipitate) {RECALL_QUERY!r} -> {len(results)} result(s):"
    )
    for r in results:
        surfaced = r.surface_text or r.internal_guidance
        print(f"   - [{r.mode.value}] score={r.score:.3f} {surfaced!r}")

    # Re-read the original to confirm its persisted end-state phase.
    original_after = engine.repo.get(original_id)  # type: ignore[attr-defined]
    assert original_after is not None

    # The "invisibility / erasure" pattern is the whole social-invisibility
    # cluster: the source dismissal experiences, their vapor abstractions, and
    # the condensed cloud. A recall is pattern-related if it surfaces any of them.
    recalled_ids = {r.droplet_id for r in results}
    pattern_ids = set(source_ids) | {v.id for v in vapors} | {cloud.id}

    return {
        # --- droplet ids ---
        "original_id": original_id,
        "primary_vapor_id": primary_vapor.id,
        "cloud_id": cloud.id,
        "vapor_ids": [v.id for v in vapors],
        "source_ids": list(source_ids),
        # --- phases (the spec's intended end-state) ---
        "original_phase": original_after.phase.value,
        "primary_vapor_phase": primary_vapor.phase.value,
        "cloud_phase": cloud.phase.value,
        # --- the evaporate derived_from link back to the original ---
        "primary_vapor_derived_from": list(primary_vapor.links.derived_from),
        # --- the cloud condensed from every vapor ---
        "cloud_members": list(cloud.meta.get("members", [])),
        "cloud_derived_from": list(cloud.links.derived_from),
        "cloud_theme": cloud.content,
        # --- recall / precipitation ---
        "recall_count": len(results),
        "recall_modes": [r.mode.value for r in results],
        "recall_is_pattern_related": bool(recalled_ids & pattern_ids),
        "recall_results": results,
    }


__all__ = ["run"]
