# Real model backends

By default HydroMemory runs **fully offline on deterministic stubs** (a hashing
embedder + heuristic text ops), so the suite is reproducible with no API key. The
embedder and the text operations are selected **independently**, so you can dial
in real models where they matter:

| Knob | Values | Default |
|---|---|---|
| `HYDRO_EMBEDDING_BACKEND` (`HydroConfig.embedding_backend`) | `stub` \| `local` | `stub` |
| `HYDRO_INTELLIGENCE_BACKEND` (`HydroConfig.intelligence_backend`) | `stub` \| `claude` | `stub` |

`build_intelligence(config)` composes the two: text ops (evaporate / classify /
contamination) come from the intelligence backend, the embedder from the
embedding backend. All-`stub` reproduces the v1 bundle exactly.

## Real embeddings (local, no API key)

`HYDRO_EMBEDDING_BACKEND=local` uses a local [sentence-transformers](https://www.sbert.net/)
model (`all-MiniLM-L6-v2`, 384-dim, unit-normalized), lazily loaded on first use.

```bash
pip install -e ".[local]"            # pulls sentence-transformers + torch (~GB)
HYDRO_EMBEDDING_BACKEND=local hydromem absorb --content "..."
```

The store's `vector_dim` must match the model (384). `HydroConfig.from_env`
defaults `vector_dim` to 384 when this backend is selected; if you construct
`HydroConfig` directly, set `vector_dim=384`.

**Why it matters.** The hash `StubEmbedder` only scores shared *tokens*; the local
model scores *meaning*. For the §12 Example A cluster, "I was dismissed during a
meeting" and "being ignored in public" share **no** words — the stub scores that
true relation ≈ 0, while the local model scores ≈ 0.32 (and an unrelated cooking
sentence ≈ 0.08). This is proven in `tests/test_real_embeddings.py` (gated; skips
if sentence-transformers isn't installed).

### Recall-ranking lever: `abstraction_bonus`

`recall.RecallWeights.abstraction_bonus` (default **0.0**) adds a bonus for
abstracted/derived phases (`vapor`/`cloud`/`groundwater`) so patterns can outrank
literal sources. The default of 0.0 keeps the literal §5.6 formula (and the golden
score test) unchanged; raise it (e.g. `0.5`) when you want recall to prefer
distilled patterns over raw episodes. Real embeddings already fix most of the gap
(semantic similarity becomes meaningful); the lever closes the structural half
(`phase_accessibility` favors `liquid`).

## Real text ops (Claude)

`HYDRO_INTELLIGENCE_BACKEND=claude` runs EVAPORATE (abstraction), classification,
and §10.1 contamination through the Anthropic Messages API.

```bash
pip install -e ".[claude]"
HYDRO_INTELLIGENCE_BACKEND=claude ANTHROPIC_API_KEY=sk-ant-... hydromem absorb --content "..."
```

- **Model:** `HYDRO_CLAUDE_MODEL` (`HydroConfig.claude_model`), default
  `claude-opus-4-7`. Pick `claude-sonnet-4-6` / `claude-haiku-4-5` for lower cost.
- **Structured output:** classification and contamination use
  `client.messages.parse()` with Pydantic schemas (validated; no JSON scraping),
  with a defensive `messages.create` + JSON fallback for older SDKs. No extended
  thinking (these are short calls).
- **`anthropic` is lazy-imported** — the module imports offline; a missing key
  raises a clear `RuntimeError` only when a Claude op actually runs.
- **Embeddings:** Anthropic has no embeddings endpoint, so the Claude backend
  keeps the stub embedder. For real semantics end-to-end, pair it with
  `HYDRO_EMBEDDING_BACKEND=local`.

**Validate it** (the offline suite can't — no key in CI):

```bash
ANTHROPIC_API_KEY=sk-ant-... ./.venv/Scripts/python.exe scripts/validate_claude.py
```

`tests/test_claude_backend.py` pins the no-key clear-error contract offline and
runs a live smoke when `ANTHROPIC_API_KEY` is set.

## Mix and match

| embedding / intelligence | result | key? |
|---|---|---|
| `stub` / `stub` | v1 default — deterministic, offline | no |
| `local` / `stub` | real semantic recall, heuristic text ops | no |
| `stub` / `claude` | real text ops, hash recall | yes |
| `local` / `claude` | real semantics end-to-end | yes |

See [ADR-0026](adr/0026-real-model-backends.md) for the design decisions.
