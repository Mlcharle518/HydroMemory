# Closing the gaps: HydroMemory vs. the four hard problems of LLM memory

> Status: **Implemented — all five gap-closure ADRs ([0030–0034](adr/README.md)) shipped (2026-05-25).**
> This document is the source of truth those ADRs and their implementations reference;
> it freezes the shared model, vocabulary, and the **spine interface contract** (§4).
> The traceability table (§3) is the original *before* analysis — each row's gap is now
> closed by its ADR (see the per-ADR statuses).

Long-context windows, RAG, and "dump the transcript into a vector DB" each fail in
a characteristic way. This document states those four failure modes precisely, shows
where HydroMemory already answers them (with citations into the running code), names
the **gap** that remains in each, and lays out the phased plan to close them.

The organizing claim: HydroMemory reframes memory as **state transitions + routing**
rather than "text in a store." That makes forgetting, consolidation, and
noise-rejection *first-class, inspectable operations* instead of properties one hopes
emerge. The reframing already pays off unevenly (strong on pollution, weaker on
multi-hop). The work below is about making the cognition that *drives* those
operations real — not re-architecting the substrate.

## 1. The four problems

1. **Context limits & degradation.** Even million-token windows degrade in the
   middle ("lost in the middle"); latency/cost scale badly; a year of conversation
   does not fit.
2. **Retrieval failure modes.** RAG misses relevant info (chunking / embedding
   mismatch), pulls irrelevant info that distracts, and struggles with **multi-hop**
   questions that must chain several facts.
3. **Memory pollution.** Bad or stale info, once stored, keeps resurfacing; there is
   no good *forgetting*; systems cannot reliably distinguish "outdated" from "rare."
4. **No consolidation.** Humans sleep and compress experience into abstractions; LLM
   systems dump raw transcripts. Agents especially **re-derive** the same conclusions
   because past reasoning is not structured for reuse.

## 2. The pattern behind HydroMemory's current standing

> **What is built and tested is the *representation and the levers*. What is stubbed
> or deferred is the *autonomic cognition that drives them*.**

- **Built (461 offline tests):** the hydraulic state vector, reservoirs/phases, the
  multi-signal recall score + per-(phase,reservoir) threshold, seven graded
  forgetting modes, structural stale-vs-rare separation, the abstraction ladder
  (`evaporate`→`condense`→`distill`→`compost`), and `WARNING` recall mode.
- **Deferred / stubbed:** multi-hop link traversal (#2), the *what-to-cluster*
  decision (#4), the *what-is-stale* selection and time decay (#3), token-budget
  context packing and ANN scale (#1) — and abstraction/detection *quality* rides on
  the model backend in every case.

Everything below targets the second list.

## 3. Traceability: problem → mechanism → gap → plan

| # | Problem | Current mechanism (cited) | What's genuinely solved | Remaining gap | Plan |
|---|---------|---------------------------|--------------------------|---------------|------|
| 1 | Context limits / lost-in-the-middle | Selective recall: `precipitate` returns a ranked, thresholded set, never a dump ([verbs.py:267](../hydromemory/verbs.py)); deep/archived droplets carry high `depth_resistance` + low `phase_accessibility` ([recall.py:44](../hydromemory/recall.py)) so they stay out of the active set | Curates *what* enters the window instead of fighting the window; cold memory self-suppresses | No token-budget packer; no primacy/recency ordering of the surfaced set; vector index is brute-force (O(N) recall), ANN deferred ([ADR-0012](adr/0012-sqlite-plus-vector-index-storage.md)) | **[ADR-0033](adr/0033-context-assembly-working-set-packing.md)** (packing) + **[ADR-0034](adr/0034-retrieval-scale-ann.md)** (ANN/scale) |
| 2 | Retrieval failure modes | Multi-signal `hydro_recall_score` subtracts contamination/privacy/depth and adds context/trigger/phase terms, gated by a threshold ([recall.py:222](../hydromemory/recall.py)); contradicted memory returns via `WARNING` mode ([recall.py:302](../hydromemory/recall.py)) | **Beats vanilla RAG on the "pulls irrelevant / distracts" axis**; flags contradictions instead of silently injecting them | **Multi-hop is unbuilt**: the `links` graph exists ([schema.py:200](../hydromemory/schema.py)) but `hydro_recall_score` scores each droplet *in isolation and never traverses an edge* — links only flip the recall *mode* | **[ADR-0030](adr/0030-query-conditioned-spreading-activation.md)** — the spine |
| 3 | Memory pollution / forgetting | Seven graded forgetting modes ([forgetting.py](../hydromemory/forgetting.py)); contamination routes to the CONTAMINATED reservoir with `purity`↓, accessibility `0.0`, threshold `0.95` ([contamination.py:33](../hydromemory/contamination.py), [recall.py:57](../hydromemory/recall.py)); `filter_droplet` repairs; `reverify` re-checks ([platform/runtime.py:116](../hydromemory/platform/runtime.py)) | **Strongest fit.** Stale-vs-rare is *structurally* separated (rare-true = groundwater/high-purity; stale-false = contaminated/low-purity) — a flat vector DB cannot do this | Detection accuracy is backend-bound (accepted); **no autonomic loop**: `aged_droplets` is a passthrough ([platform/runtime.py:139](../hydromemory/platform/runtime.py)) and there is **no time-based decay** | **[ADR-0032](adr/0032-time-decay-autonomic-forgetting.md)** |
| 4 | No consolidation | Abstraction ladder: `evaporate`→VAPOR, `condense`→CLOUD, `distill`→principle in CLOUD ([ADR-0036](adr/0036-distilled-principles-land-in-cloud.md); was SACRED), `compost`→lesson ([verbs.py:194,231,452](../hydromemory/verbs.py), [forgetting.py:117](../hydromemory/forgetting.py)); `abstraction_bonus` lets a derived principle outrank its sources ([recall.py:108](../hydromemory/recall.py)) | A distilled principle is reusable structured reasoning → directly attacks "agents re-derive" | The *what-to-consolidate* decision is unbuilt: `condense`/`distill` take an **explicit** member list, and `engine.cluster` (called at [agents/distillation.py:30](../hydromemory/agents/distillation.py)) **has no implementation**; no consolidation cadence | **[ADR-0031](adr/0031-autonomic-consolidation.md)** (rides on the §4 spine for grouping) |

**The leverage insight:** #2 and #4 share a root. Query-conditioned traversal over the
`links` graph closes multi-hop retrieval **and** produces the connected-subgraph
grouping that the missing `cluster` primitive needs. So the spreading-activation
spine ([ADR-0030](adr/0030-query-conditioned-spreading-activation.md)) is built first
and unblocks consolidation ([ADR-0031](adr/0031-autonomic-consolidation.md)).

## 4. The spine: query-conditioned spreading activation (FROZEN CONTRACT)

This section is the interface contract. ADR-0030 and the implementation
(`hydromemory/activation.py`) must conform to it; ADR-0031's `cluster` builds on it.

### 4.1 The model — "water through the roots"

A corpus *is* memory; the answer to a question rarely lives in one droplet — it lives
in how a *constellation* of droplets interacts (a clause needs its definitions, its
exceptions, the amendment that superseded it). The question is **water entering at the
matched droplets**; the `links` are the **root network**; activation propagates through
the connected subgraph and decays with distance; the surfaced answer is the
constellation that activates *together*. Which constellation lights up is determined by
the external entity — the question — exactly the protocol's own thesis ("memory is
information moving through state, and the state is driven by context"). This generalizes
the existing recall *modes* from *how one droplet surfaces* to *which group activates*.
See the originating note: [research/memory-as-interacting-network.md](research/memory-as-interacting-network.md).

### 4.2 The hydraulic parameterization

The state vector already *is* the natural parameterization of flow; we do not invent
new knobs:

- `fluidity` → **conductance**: how readily activation flows *out* of a node.
- `depth` → **resistance**: how much a target damps incoming activation.
- `purity` (and `salinity`) → **mixing / epistemic hygiene**: a low-purity node
  contributes *less* of its activation downstream, so contaminated memory cannot flood
  the constellation it flows into.
- `pressure` / `gravity` → seed weighting (a high-head memory pulls harder at entry).

### 4.3 The module and signatures

New module `hydromemory/activation.py`. Pure functions, dependency-injected accessors
(no direct repo import), deterministic, offline — same testability discipline as
`recall.py`/`pipeline.py`.

```python
@dataclass(frozen=True)
class ActivationParams:
    max_hops: int = 3            # graph radius from the seeds
    decay: float = 0.5          # base per-hop activation multiplier
    min_activation: float = 0.05  # prune nodes below this
    max_nodes: int = 64         # cap constellation size (cost guard)
    purity_damping: float = 1.0   # exponent on source.purity for its contribution
    edge_weights: Mapping[str, float] = (   # per link-kind base conductance
        {"associations": 1.0, "supports": 0.9, "derived_from": 0.7, "contradictions": 0.6})
    intent_edges: Mapping[str, tuple[str, ...]] = ...  # intent -> preferred kinds (4.4)

DEFAULT_ACTIVATION_PARAMS = ActivationParams()

def spread_activation(
    seeds: Mapping[str, float],                               # entry id -> seed activation
    neighbors: Callable[[str], Sequence[tuple[str, str]]],    # id -> [(neighbor_id, link_kind)]
    states: Callable[[str], "State | None"],                  # id -> hydraulic State
    *,
    intent: str | None = None,
    params: ActivationParams = DEFAULT_ACTIVATION_PARAMS,
) -> dict[str, float]:
    """Spread activation from seeds over the links graph; return id -> activation
    for every reached node (seeds included). Frontier-based, decaying, pruned."""

def cluster(
    droplets: Sequence["Droplet"],
    neighbors: Callable[[str], Sequence[tuple[str, str]]],
    *,
    params: ActivationParams = DEFAULT_ACTIVATION_PARAMS,
) -> list[list["Droplet"]]:
    """Group droplets into constellations (connected components over the
    association/support/derived_from subgraph, refined by activation co-occurrence).
    This is exactly the engine.cluster surface DistillationAgent calls."""
```

### 4.4 Propagation rule (per delivered edge)

```
edge_w      = effective_edge_weight(kind, intent, params)   # intent_edges gates/boosts kinds
conductance = source_state.fluidity
resistance  = target_state.depth
mix         = source_state.purity ** params.purity_damping
delivered   = source_activation * params.decay * edge_w * conductance * (1 - resistance) * mix
activation[target] += delivered            # accumulate; a node reached by two paths sums
```

Iterate hop-by-hop carrying only newly-delivered activation; prune `< min_activation`;
stop at `max_hops` or once `max_nodes` are active. **Intent → edge selection** (default
`intent_edges`, extensible): `"exception"`→`(contradictions, derived_from)`;
`"evidence"|"support"`→`(supports, associations)`;
`"definition"|"meaning"|"related"`→`(associations, supports, derived_from)`;
`"currency"|"supersede"`→`(derived_from, contradictions)`; `None`→all kinds at base
weight. (Our four link kinds are the substrate; richer edge types like `defines` /
`supersedes` are a future extension noted in ADR-0030, not a prerequisite.)

### 4.5 How it rides on recall WITHOUT rewriting the §5.6 score

Additive and **opt-in**, mirroring how `abstraction_bonus` was introduced
([ADR-0026](adr/0026-real-model-backends.md)) so golden score tests stay exact:

1. Add `RecallWeights.activation_bonus: float = 0.0`. **Default 0.0 ⇒ v1 behavior is
   byte-identical** and `hydro_recall_score` is untouched in shape.
2. When `precipitate` runs with traversal enabled (`traverse=True` or
   `activation_bonus > 0`): seed `spread_activation` from the `search_similar` top-k
   (seed value = cosine), traverse the repo's `links`, obtaining an activation map that
   **can include droplets the cosine top-k missed** — the multi-hop win.
3. Every activated droplet (seeds ∪ reached) is fetched, permission-gated, and scored
   by the *existing* `hydro_recall_score`, plus `activation_bonus * activation[id]`, and
   kept only if it clears `recall_threshold` as today. Recall modes render unchanged.

So the §5.6 score keeps its exact form; traversal is a candidate-expansion step plus one
additive term. Brute-force entry retrieval is unchanged (ANN is [ADR-0034](adr/0034-retrieval-scale-ann.md)).

### 4.6 Invariants (the parallel tracks must preserve)

- **Default-off additivity** (ADR-0025): all-default config ⇒ 461 tests still pass.
- **Permission parity:** a traversal-reached droplet is gated by the same
  `check_access`/permission path as a cosine hit — traversal never widens access.
- **Hygiene:** low-purity nodes spread less (4.4 `mix`); a `POLLUTED` node
  (accessibility 0, threshold 0.95) cannot anchor or dominate a constellation.
- **Determinism / offline:** no network, stable ordering, seedable.

## 5. Phased roadmap (Now / Next / Later)

**Now — Phase 1: the spine.** Implement `activation.py` (`spread_activation` +
`cluster`), wire the opt-in `activation_bonus` traversal into `precipitate`, tests for
multi-hop recall, hydraulic decay, contamination damping, and intent→edge selection.
Closes #2 (multi-hop); delivers the `cluster` primitive #4 needs.
→ [ADR-0030](adr/0030-query-conditioned-spreading-activation.md).

**Next — Phase 2: autonomic cognition.**
- Consolidation cadence — **built**: `MeshEngine.cluster`/`distill` + the tick-path
  (`DistillationAgent` over `AgentRuntime.tick("distill")`), plus the opt-in bus
  auto-trigger — `Mesh(consolidate=True)` gather-then-distills dense constellations
  into CLOUD principles on `ABSORBED` (density-gated, cascade-bounded; principal-written).
  → [ADR-0031](adr/0031-autonomic-consolidation.md), reservoir refined by
  [ADR-0036](adr/0036-distilled-principles-land-in-cloud.md) (SACRED → CLOUD so ordinary
  approved agents can reuse them).
- Decay + real `aged_droplets` — **built**: `forgetting.decay` (salience-only,
  truth-preserving) + `forgetting.select_aged` (real store query), wired into
  `MeshEngine` (optional read-only repo + a `decay` surface). The periodic driver
  (a scheduled maintain pass) is external. → [ADR-0032](adr/0032-time-decay-autonomic-forgetting.md).

**Later — Phase 3: context & scale.**
- Working-set packer — **built**: `packing.pack_working_set` (pure; token budget +
  provenance dedup + abstraction preference + primacy/recency placement; default
  passthrough, standalone post-recall step). → [ADR-0033](adr/0033-context-assembly-working-set-packing.md).
- ANN index — **built + validated**: `VectorIndexProtocol` + `build_vector_index`
  factory + two backends (`AnnVectorIndex`/hnswlib, `FaissVectorIndex`/faiss); brute-force
  stays the exact default (`config.vector_backend`). Validated locally with faiss-cpu
  (recall@k parity + remove/replace/persist round-trip). → [ADR-0034](adr/0034-retrieval-scale-ann.md).

## 6. Evaluation (how we'll know a gap actually closed)

> These are assertion-level checks (realized as unit tests). The **efficacy** harness that
> turns them into measured numbers against baselines, over a distribution of cases, is
> designed in [evals/README.md](../evals/README.md) — correctness here, efficacy there.

- **#2 multi-hop:** a seeded fixture where the answer requires chaining ≥2 linked
  droplets none of which is individually top-k by cosine; assert the constellation
  surfaces with traversal on and fails to with it off.
- **#3 forgetting:** assert a stale/contaminated droplet stops surfacing after decay/
  reverify while a rare-but-true droplet (low salience, high purity) still surfaces on
  a strong pull — the stale-vs-rare separation holds.
- **#4 consolidation:** assert `cluster` groups a known constellation and the resulting
  distilled principle outranks its literal sources at recall (`abstraction_bonus` > 0).
- **#1 packing/scale:** assert the packer respects a token budget and orders by
  importance to the edges; assert ANN recall@k matches brute-force on a fixture corpus.
- **Global invariant:** the existing suite stays green on all-default config (ADR-0025).

## 7. Scope discipline

All five gap-closure ADRs (0030–0034) are now implemented (default-off, additive —
[ADR-0025](adr/0025-additive-layering-v1-stays-green.md)): the spreading-activation
spine, autonomic consolidation, salience decay, the working-set packer, and the
pluggable ANN seam. Quality of abstraction/detection remains backend-bound (stub vs.
local/Claude — [ADR-0026](adr/0026-real-model-backends.md)); the ANN seam is validated
locally with faiss-cpu (parity + round-trip green), while the hnswlib backend stays
wired-but-unexercised without a C++ toolchain. We built the *mechanisms that drive the
levers*, not new intelligence backends.
