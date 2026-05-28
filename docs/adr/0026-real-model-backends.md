# ADR-0026: Real model backends (composable factory, local embeddings, hardened Claude)

Status: Accepted

## Context

v1/v2 ran entirely on deterministic stubs: a hashing `StubEmbedder` (token overlap,
not meaning) and heuristic text ops. That keeps the suite offline and reproducible,
but leaves recall/abstraction/contamination quality unproven, and the `claude`
backend (written in v1) had never actually run — it scraped JSON with a regex and
hard-coded a model. We wanted to *prove* behavior with real models without breaking
the offline default.

## Decision

- **Composable intelligence factory.** `build_intelligence` selects the embedder
  (`embedding_backend` ∈ `stub|local`) and the text ops (`intelligence_backend` ∈
  `stub|claude`) **independently**, then composes them. All-`stub` is byte-identical
  to v1, so the 386 tests are unaffected.
- **Local real embeddings.** `LocalEmbedder` wraps sentence-transformers
  (`all-MiniLM-L6-v2`, 384-dim, lazy-loaded) behind the `local` extra. `from_env`
  defaults `vector_dim` to 384 when selected (the store dim must match the model).
  Chosen over a hosted embedding API because it needs no key — so real semantic
  recall can be demonstrated locally (`tests/test_real_embeddings.py`).
- **`abstraction_bonus` recall lever.** A new `RecallWeights.abstraction_bonus`
  (default **0.0**) adds a bonus for `vapor`/`cloud`/`groundwater`. Default 0.0
  keeps the literal §5.6 formula and the golden score test exact; raising it closes
  the documented "literal beats abstraction" gap. Implemented as an additive term
  rather than a default re-weighting precisely so nothing existing changes.
- **Hardened Claude backend.** Structured output via `client.messages.parse()` +
  Pydantic schemas (with a defensive `create` + JSON fallback for older SDKs);
  model configurable via `HYDRO_CLAUDE_MODEL`, defaulting to `claude-opus-4-7`
  (per the Claude-API skill — never downgrade for cost on the user's behalf; they
  can choose Sonnet/Haiku); no extended thinking; `anthropic`/`pydantic`
  lazy-imported; a clear `RuntimeError` only when run without a key.

## Consequences

- The default path is unchanged and fully offline; real backends are strictly
  opt-in via env/config. 391 tests pass (1 live-Claude smoke skips without a key).
- `local` pulls in PyTorch (~GB); it's an optional extra, gated/skipped in CI.
- The local model fixes the *similarity* half of the recall gap; `abstraction_bonus`
  is the lever for the *structural* half (phase-accessibility favoring `liquid`).
- The Claude backend is wired and hardened but **not exercised in CI** (no key);
  `scripts/validate_claude.py` + the gated test are the human verification path.
- Anthropic has no embeddings endpoint, so `claude` text ops keep the stub embedder
  unless paired with `embedding_backend=local`.
