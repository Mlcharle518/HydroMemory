# ADR-0006: Recall terms are unweighted by default; per-term clamp to [0,1]

Status: Accepted

## Context

The §5.6 recall formula is a sum of ten terms (seven added, three subtracted) but
the PRD assigns no relative weights and does not specify the range or scaling of
each term. The terms are heterogeneous: some are cosine similarities, some are
raw state floats, some are Jaccard overlaps. Summed naively, an unbounded term
could dominate, and the formula would not be reproducible.

## Decision

Introduce a `RecallWeights` dataclass with one weight per term, **all defaulting
to `1.0`** — i.e. the default behavior is the unweighted §5.6 sum, faithfully.
Every term is **clamped to `[0,1]` before it is weighted and summed**
(`semantic_similarity`, `permission_score`, `privacy_risk`, and
`contamination_penalty` are clamped on entry; the derived terms are clamped where
computed). Callers may pass custom weights to tune recall without touching the
formula.

## Consequences

- The default is exactly the spec formula, so the implementation is a literal
  reading of §5.6.
- Clamping makes the score bounded and reproducible, and keeps any single term
  from dominating.
- Weighting is a future-facing extension point (e.g. learned weights) that
  requires no code change to the scorer.
