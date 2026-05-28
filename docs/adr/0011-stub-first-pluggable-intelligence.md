# ADR-0011: Stub-first pluggable intelligence; Claude backend lazy-imported

Status: Accepted

## Context

HydroMemory needs real NLP for four operations: embeddings (semantic similarity),
abstraction (EVAPORATE), §6 classification, and §10.1 contamination assessment.
Wiring these directly to a hosted model (e.g. Claude) would make the engine
require network access and an API key just to run, break CI and offline use, and
make tests nondeterministic. But the PRD clearly intends real intelligence to be
pluggable.

## Decision

Express the four operations behind small ABCs (`Embedder`, `Abstractor`,
`Classifier`, `ContaminationDetector`) bundled into an `Intelligence` object, and
default to a **deterministic, offline stub** backend. `build_intelligence(config)`
selects the backend from `HYDRO_INTELLIGENCE_BACKEND` (default `stub`). The
**Claude backend is imported lazily**: `build_intelligence` imports the Claude
module only when `backend == "claude"`, and that module imports `anthropic`
*inside each method*. Anthropic has no embeddings endpoint, so the Claude bundle
reuses the deterministic `StubEmbedder` for vectors.

## Consequences

- The engine runs fully offline with no API key, so CI and a laptop work out of
  the box; stub outputs are reproducible across processes (SHA-256 hashing-trick
  embeddings, never the salted builtin `hash`).
- A missing `anthropic` package or `ANTHROPIC_API_KEY` never breaks the default
  path — the error appears only when a Claude-backed method is actually called.
- Swapping in another backend (or a real embeddings provider) is a matter of
  implementing the four ABCs; the engine, pipeline, and verbs are unchanged.
