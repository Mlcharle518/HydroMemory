# ADR-0033: context assembly / working-set packing

Status: Accepted — implemented (see ../closing-the-gaps.md §5)

> **Implemented 2026-05-25.** `hydromemory/packing.py` — `pack_working_set(results,
> *, token_budget=None, get_droplet=None, tokenizer=None)` is a pure function over
> `precipitate`'s `RecallResult` list. No budget → **passthrough** (v1 unchanged);
> with a budget → provenance-dedup (drop a source whose distilled principle is
> present, via `links.derived_from`), greedy fill **preferring abstracted/principle**
> droplets, charging the budget **only against surfaced** (`show_to_user`) text, then
> **primacy/recency** placement (best at both edges, weakest in the middle).
> Tokenizer pluggable (char-count default). Tests `tests/test_packing.py`; suite
> 491→497. It is a **standalone post-recall step** — not wired into `precipitate` by
> default; the caller passes `get_droplet=repo.get`. As below, this shapes the block
> but cannot fix attention rot *inside* the model.

## Context

HydroMemory's answer to context limits is **selective recall**: `precipitate`
([verbs.py:267](../../hydromemory/verbs.py)) returns a *ranked, thresholded* set of
`RecallResult` objects, never a transcript dump. That curates **what** enters the
window instead of fighting the window — the right posture, and the strong half of
problem #1 ([closing-the-gaps.md §3 row 1](../closing-the-gaps.md)).

It does **not** address attention degradation. `precipitate` sorts by score and
stops ([recall.py:312](../../hydromemory/recall.py)); nothing packs that list to a
token budget or decides *placement order*. A caller that just concatenates the
ranked list re-opens the "lost in the middle" failure mode — the very degradation
selective recall was supposed to sidestep — because the model under-attends to the
center of a long block regardless of how good the items are. This ADR records the
**assembly** half of #1; retrieval **scale** (ANN) is [ADR-0034](0034-retrieval-scale-ann.md).

## Decision

Add a working-set **packer**: a pure function over the ranked `RecallResult` list
plus a token budget. It consumes recall output and **does not touch scoring or
thresholds** (those stay in `hydro_recall_score` / `recall_threshold`). It:

1. **Prefers abstracted/principle droplets** — `RecallMode.PATTERN` and the
   vapor/cloud/groundwater distilled principles — so each token buys more meaning;
   this composes with `abstraction_bonus` ([ADR-0026](0026-real-model-backends.md))
   already letting a principle outrank its sources.
2. **Dedupes by provenance** via `links.derived_from`
   ([schema.py:205](../../hydromemory/schema.py)): when a source *and* its distilled
   principle both clear recall, keep the principle, drop the redundant source.
3. **Orders for primacy/recency** — highest-value items at the **start and end** of
   the assembled block, lower-value toward the middle — directly countering
   lost-in-the-middle by *shaping where reliance falls*.
4. **Respects recall modes** — `SILENT`/`BEHAVIORAL` guidance stays internal-only
   (`show_to_user=False`); only surfaceable text is emitted into the block.

The tokenizer is **pluggable** (character-count default, offline; a real tokenizer
optional, mirroring the embedder split in [ADR-0026](0026-real-model-backends.md)).
The **default is passthrough** — no budget set ⇒ the ranked list is returned in
score order, so v1 behavior and the test suite are byte-identical ([ADR-0025](0025-additive-layering-v1-stays-green.md)).

## Consequences

- Attacks lost-in-the-middle by **shaping the assembled block** (placement +
  budget), **complementing, not replacing**, selective recall — recall still decides
  membership; the packer only orders and trims what recall already passed.
- Budget and primacy/recency ordering are **documented-default policies** (like the
  phase/reservoir threshold tables), tunable, not load-bearing magic.
- Provenance-dedup **leans on the `links` graph**; a missing `derived_from` edge
  means a source and its principle can both survive — dedup is best-effort, bounded
  by link quality, never a correctness guarantee.
- The tokenizer is pluggable and **offline-friendly**; char-count is approximate, so
  a budget is a soft guard, not an exact model-token count until a real tokenizer is
  injected.
- Default passthrough keeps the suite green and makes adoption opt-in.
- **Honest limit:** the packer cannot fix attention rot *inside* the model. It only
  reduces how much you place in the middle and how much you rely on it — the model's
  center-of-context weakness itself is untouched. Assembly only; scale is [ADR-0034](0034-retrieval-scale-ann.md).
