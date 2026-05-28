# ADR-0031: autonomic consolidation (cluster primitive + cadence)

Status: Accepted — implemented (see ../closing-the-gaps.md §5)

> **Superseded in part by [ADR-0036](0036-distilled-principles-land-in-cloud.md)
> (2026-05-26).** This ADR landed distilled principles in **SACRED**. The consolidation
> efficacy eval showed that defeated this ADR's *own reuse goal*: SACRED is governance-gated,
> so an ordinary APPROVED recall identity (the `Engine.answer` default) could never recall the
> principles — `principle_present_rate` was 0.00 under an approved agent. ADR-0036 moves
> distilled principles to **CLOUD** (the abstraction layer, approved-agent readable), keeping
> `phase=GROUNDWATER`. Wherever this document says principles land in SACRED, read **CLOUD**;
> the cluster primitive, cadence, density gate, and cascade-safety below are unchanged.

> **Implemented 2026-05-25 (Phase 2).** All three decision items are built.
> *Item 1 + tick path:* `MeshEngine.cluster` delegates to `activation.cluster` and
> `MeshEngine.distill` builds a storage-free `SACRED` principle, so
> `DistillationAgent` runs `cluster`→`distill` via `AgentRuntime.tick("distill")`.
> *Item 2 (the bus auto-trigger):* `Mesh(consolidate=True)` adds an opt-in, many→one
> *gather-then-distill* reaction the single-droplet `Reaction` table cannot express —
> on `ABSORBED` it gathers the seed's linked constellation, **density-gates on
> neighborhood size** (the operational form of the `DENSITY` notion, since the bus
> carries `EventType`s, not `Trigger`s), `cluster`→`distill`s each component, and
> emits `DISTILLED`. Because a SACRED write requires a user-proxy (which the mesh's
> working vault — e.g. a filtration identity — is not), principles are written
> through a derived user-proxy view (`Mesh._principal_vault`): autonomic
> consolidation is, correctly, a user-proxy act. *Item 3:* the reaction reuses the
> depth guard, per-cycle dedupe, and skip-principle/terminal guards, so it cannot
> storm. Default-off (ADR-0025). Tests: `tests/test_consolidation.py` +
> `tests/test_mesh_consolidation.py`; suite 461→481.

## Context

The abstraction ladder (`evaporate`→`condense`→`distill`→`compost`) is built and
tested, and `abstraction_bonus` (ADR-0026) lets a derived principle outrank its
literal sources at recall. But the ladder cannot run itself: `condense` and
`distill` (`verbs.py`) require the **caller to pass the member list** — the system
has no way to decide *what* to consolidate. `DistillationAgent` already assumes the
missing piece, calling `self.engine.cluster(droplets, ctx.payload)`
(`agents/distillation.py`), but **no `cluster` is implemented anywhere** — on the
mesh's `MeshEngine` (`platform/runtime.py`) that call raises `AttributeError`. And
there is no consolidation cadence at all, so nothing ever fires the ladder
unprompted. This is gap #4 (closing-the-gaps.md §3): with past reasoning never
structured for reuse, agents **re-derive** the same conclusions.

## Decision

1. **Implement the `cluster` primitive.** Give the engine (and `MeshEngine`) a
   `cluster(droplets, context)` that delegates to `hydromemory/activation.py`'s
   `cluster(...)` — connected components over the `associations`/`supports`/
   `derived_from` subgraph, refined by activation co-occurrence, per the frozen §4.3
   contract. This is exactly the surface `DistillationAgent` calls; it comes from the
   spine, not from new grouping logic invented here.
2. **Add an autonomic consolidation cadence.** On a mesh tick — or when a `DENSITY`
   or `SIMILARITY` synthetic trigger fires (reuse `triggers.py`; do not invent new
   triggers) — run `cluster` → `evaporate`/`condense` → `distill`, landing a
   principle droplet in the `SACRED` reservoir with `derived_from` provenance. The
   reasoning is then stored as a reusable principle, and `abstraction_bonus`
   (ADR-0026) lets it outrank its sources at recall.
3. **Bound it with the existing mesh cascade-safety (ADR-0024).** Consolidation runs
   under the same four mechanisms — depth guard, per-cycle dedupe, no-op suppression,
   terminal-phase guard — so a distilled principle (a `DISTILLED` event) cannot storm
   into further consolidation. The cadence is **opt-in / config-gated** so the
   default path is byte-identical and the suite stays green (ADR-0025).

## Consequences

- Closes the consolidation-autonomy gap (#4): clustering becomes a decision the
  system makes, not an argument the caller supplies, and `DistillationAgent` stops
  being a call into the void.
- **Depends on ADR-0030.** The `cluster` primitive *is* the spine's `cluster(...)`;
  this ADR cannot land before `activation.py` exists. The cadence is the only net-new
  cognition here — grouping is borrowed.
- The cadence is bounded by ADR-0024, so it inherits the mesh's storm guarantees for
  free; raising `max_depth` deliberately widens the allowed chain as before.
- **Over-consolidation risk** (collapsing distinct memories into one mushy principle)
  is managed by the `DENSITY`/`SIMILARITY` thresholds in `triggers.py` /
  `PhaseConfig`; tune the thresholds up if principles form too eagerly.
- Abstraction **text quality** stays backend-bound (ADR-0026): the stub abstractor
  joins/heuristically summarizes, while the Claude backend produces a real principle.
  The cadence fires correctly either way; only the prose improves with a real model.
- Default-off keeps the 461-test suite unchanged (ADR-0025); only an explicitly
  enabled cadence consolidates.
- Principles accumulate in `SACRED` over time, so a future pass will likely need
  principle-dedup / superseding (an old principle retired when a better one is
  distilled). We note that here but do **not** solve it in this ADR.
