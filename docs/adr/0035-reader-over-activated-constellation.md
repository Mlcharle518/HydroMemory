# ADR-0035: reader — compose answers over the activated constellation (with citations)

Status: Accepted

## Context

Recall — including the ADR-0030 spreading-activation traversal — returns a *set* of
droplets (the constellation that answers a question). Nothing turned that set into an
*answer*. The `multihop_qa` efficacy eval showed why this matters (traversal lifts
supporting-fact recall +0.33 but end-to-end answer coverage only +0.10): the
retrieval→answer step is where the benefit is realized or lost, and it had no
first-class home. The research note's §5 "composition / reader step" was exactly this
unbuilt piece, and the eval's reader was inline/throwaway.

## Decision

Promote the reader into the library as `hydromemory/reader.py`:

- `compose_answer(query, droplets, *, composer, max_context)` → `ReaderResult(answer,
  citations, context_ids)`. The composer turns (query, numbered context items) into
  answer text and may cite items as `[n]` (1-based); `compose_answer` maps those back
  to **droplet ids** so callers get verifiable provenance (`citations`).
- **Composition is pluggable, stub-first** (same spirit as ADR-0011/0026): the offline
  default `_extractive_composer` is deterministic (surface + cite the top droplet, no
  network); `build_composer(config)` returns a lazy **Claude** composer when
  `intelligence_backend='claude'`, else `None` (extractive). The Claude composer prompts
  for `[n]` citations.
- **`Engine.answer(query, *, traverse=True, weights, composer, k)`** wires it: recall via
  `Verbs.precipitate` with spreading-activation traversal on by default (plus a default
  `activation_bonus` so the constellation contributes), then `compose_answer` over the
  recalled droplets. Purely additive — a new method + module; no existing path changes.

## Consequences

- Recall becomes **end-to-end QA over the activated constellation**, with citations —
  finishing the research note's composition step at the reference-impl level.
- The offline extractive default keeps it **testable and key-free** (`tests/test_reader.py`);
  real abstractive answers need the Claude composer (LLM-gated). The `multihop_qa` eval now
  drives the *promoted* reader instead of an inline one.
- **Answer quality is backend-bound** (extractive stub vs. Claude) — out of scope here, as
  with every other intelligence-backed step (ADR-0026).
- **Citation fidelity depends on the composer** emitting `[n]` honestly; an LLM may
  under- or over-cite. `compose_answer` only clamps to the provided range — it does not
  verify the answer is *entailed* by the cited droplets (a future, harder check).
- Richer pieces of the "memory as interacting network" frontier remain future work:
  learned corpus→links extraction, query-conditioned edge-type selection, and an
  entailment/grounding check on citations.
