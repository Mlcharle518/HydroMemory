# ADR-0030: query-conditioned spreading activation over links

Status: Accepted

## Context

`hydro_recall_score` scores each droplet *in isolation*. It reads that droplet's own
state, triggers, and context terms; it never traverses the `links` graph. The graph
(`Links.associations` / `contradictions` / `supports` / `derived_from`) does exist on
every `Droplet`, but today it touches recall only through `select_recall_mode`, where a
`contradictions` edge flips the *mode* to `WARNING`. So the schema draws the roots and
the water never flows through them: a question whose answer must chain several linked
facts — a clause plus its definition plus the amendment that superseded it — fails,
because none of those droplets is individually top-k by cosine. This is gap #2 (multi-hop)
in `../closing-the-gaps.md`.

Separately, the autonomic consolidation of `0031-autonomic-consolidation.md` needs a
grouping primitive — `engine.cluster`, called by `DistillationAgent` — that has no
implementation: `condense`/`distill` take an *explicit* member list today. Both gaps
share one root cause, and one fix: query-conditioned traversal over `links` closes
multi-hop retrieval *and* yields the connected-subgraph grouping `cluster` requires. This
ADR records the frozen spine contract of `../closing-the-gaps.md` §4 and is implemented
in Phase 1 (now).

## Decision

Introduce **query-conditioned spreading activation** over the `links` graph in a new
module `hydromemory/activation.py` — pure, dependency-injected, deterministic, offline,
same discipline as `recall.py`. The question is water entering at the matched droplets;
`links` are the root network; activation propagates through the connected subgraph and
decays with distance; the constellation that lights up *together* is the answer, and
which constellation lights up is determined by the question.

The hydraulic state vector *is* the parameterization — no new knobs. `fluidity` is
**conductance** (how readily activation flows out of a node); `depth` is **resistance**
(how much a target damps incoming flow); `purity` (with `salinity`) is **mixing /
epistemic hygiene** (a low-purity node contributes less downstream, so contamination
cannot flood the constellation it flows into). We add `spread_activation(seeds, neighbors,
states, *, intent, params)` and `cluster(...)` exactly as in §4.3, governed by an
`ActivationParams` (`max_hops=3`, `decay=0.5`, `min_activation=0.05`, `max_nodes=64`,
`purity_damping=1.0`, per-kind `edge_weights`, and an `intent_edges` map). The per-edge
propagation rule from §4.4 is::

    delivered = source_activation * decay * edge_w * fluidity * (1 - depth) * purity**damping

accumulated at the target (a node reached by two paths sums), carrying only newly-delivered
activation hop-by-hop, pruning `< min_activation`, stopping at `max_hops` or `max_nodes`.
`intent` gates which edge kinds are followed (§4.4): `"exception"`→`(contradictions,
derived_from)`; `"evidence"|"support"`→`(supports, associations)`;
`"definition"|"meaning"|"related"`→`(associations, supports, derived_from)`;
`"currency"|"supersede"`→`(derived_from, contradictions)`; `None`→all kinds at base weight.

Integration is **additive and opt-in**, mirroring how `abstraction_bonus` was introduced
in `0026-real-model-backends.md` so golden score tests stay exact. We add
`RecallWeights.activation_bonus: float = 0.0`. **Default 0.0 ⇒ v1 recall is byte-identical**
and `hydro_recall_score` keeps its §5.6 shape. When `precipitate` runs with traversal
enabled (`traverse=True` or `activation_bonus > 0`), it seeds `spread_activation` from the
`search_similar` top-k (seed value = cosine), spreads over the repo's `links`, and obtains
an activation map that *can include droplets the cosine top-k missed* — the multi-hop win.
Every activated droplet (seeds ∪ reached) is then fetched, permission-gated, and scored by
the *existing* `hydro_recall_score` plus `activation_bonus * activation[id]`, kept only if
it clears `recall_threshold` as today; recall modes render unchanged. Traversal is a
candidate-expansion step plus one additive term — not a rewrite of the score.

The §4.6 invariants hold: **default-off additivity** (ADR-0025 — all-default config leaves
the suite green); **permission parity** (a traversal-reached droplet passes the same
`check_access` path as a cosine hit — traversal never widens access); **hygiene** (low-purity
nodes spread less via the `purity**damping` term, so a `POLLUTED` node — accessibility 0,
threshold 0.95 — cannot anchor or dominate a constellation); **determinism / offline** (no
network, stable ordering, seedable).

## Consequences

- Closes the multi-hop gap (#2): a constellation whose members are none of them top-k by
  cosine now surfaces with traversal on, and demonstrably fails to with it off.
- Delivers the `cluster` primitive that `0031-autonomic-consolidation.md`'s autonomic
  consolidation needs — connected components over the association/support/derived_from
  subgraph, refined by activation co-occurrence — so Phase 2 is unblocked.
- `activation_bonus` defaults to **0.0**, so the pre-existing tests stay green and the §5.6
  golden score is unchanged; traversal is strictly something a caller opts into. The 13 new
  spine tests (`tests/test_activation.py`) bring the suite to 473 passed + 1 skipped.
- Entry retrieval stays brute-force O(N); ANN behind the `VectorIndex` contract is
  `0034-retrieval-scale-ann.md`, not this ADR.
- Cost is bounded by `max_hops` / `max_nodes` / `min_activation`; an adversarial or
  densely-linked corpus cannot expand the constellation without bound.
- Our four link kinds are the substrate. Richer edge semantics (`defines`, `supersedes`)
  would sharpen intent→edge selection but are a future extension, not a prerequisite.
- This buys *connectivity*, not comprehension: the activated constellation is the right set
  of droplets, but the *quality* of the composed answer (and of the abstractions
  consolidation later distills from it) remains backend-bound — stub vs. local/Claude,
  per `0026-real-model-backends.md`.
