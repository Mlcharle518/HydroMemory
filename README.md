# HydroMemory

An open-source **hydraulic memory engine for AI**. A memory is a **droplet** with a physics-style
state vector that moves through **phases** (liquid, vapor, cloud, rain, groundwater, ice, …) across
**reservoirs** (working stream → surface → groundwater → glacier → ocean → contaminated → sacred).
Recall scores phase accessibility, contextual fit, permissions, contamination, and privacy — not
just embedding similarity.

> Memory is not stored information; memory is information moving through state.

Apache-2.0. The default intelligence backend is a deterministic, offline **stub**, so everything
runs with **no API key**.

## Why it's different

- **Recall is more than similarity** — it weighs phase accessibility, reservoir/permission scope,
  contamination, and privacy alongside semantic fit.
- **Multi-hop recall** via query-conditioned spreading activation over the memory graph.
- **Consolidation** distills repeated droplets into durable principles; **salience-only decay**
  fades pressure/fluidity without erasing meaning or provenance.
- **Governed by design** — reservoir-scoped permissions, an opt-in encrypted user-owned vault, and
  a fail-closed event bus.
- **Pluggable** — deterministic stub by default; opt-in local MiniLM embeddings (`.[local]`),
  Claude text-ops (`.[claude]`), and an ANN backend (`.[ann]`).

## Quickstart

```bash
python -m venv .venv
# Windows (git-bash): source .venv/Scripts/activate  |  PowerShell: .\.venv\Scripts\Activate.ps1
# macOS / Linux:      source .venv/bin/activate
pip install -e ".[dev]"

hydromem init                  # interactive wizard: writes hydromemory.toml + .env
# or, take all defaults (offline stub) and skip prompts:
hydromem init --non-interactive --preset offline

pytest                         # offline test suite
hydromem run-example A         # a PRD §12 source example (A–F)
```

The wizard captures backend choices (storage, embeddings, vector index, vault,
default identity, cycle tick) and writes a `hydromemory.toml` + `.env` pair.
Re-running it uses the existing values as defaults — safe to repeat.

Drive it from Python:

```python
from hydromemory.engine import build_engine
from hydromemory.config import HydroConfig

eng = build_engine(HydroConfig())
eng.verbs.absorb("Dismissed during a meeting.", source="experience")
hits = eng.verbs.precipitate(query="being ignored in public", agent="assistant")
```

…or through the canonical SDK:

```python
from hydromemory.sdk import HydroClient

with HydroClient() as hc:
    hc.absorb("User prefers concise answers.")
    print(hc.which_verbs())     # the canonical protocol verbs available on this engine
```

## What's inside

- **Memory engine** — the droplet lifecycle, the API verbs, 7 recall modes, the HQL query
  language, reservoir governance, contamination + forgetting, an agent runtime, a CLI, and an
  optional FastAPI HTTP boundary.
- **Canonical layer** — a shared object envelope (§8 minimum metadata) + a protocol-verb registry
  + JSON schemas, and a permission-gated **cognitive event bus**.
- **SDK** — `HydroClient` drives the engine through the canonical verbs (`validate` / `canonical` /
  `which_verbs` / `events`).

## Documentation

- [docs/README.md](docs/README.md) — overview, CLI, examples, server + TS client
- [docs/architecture.md](docs/architecture.md) — the hydraulic memory model, layers, pipelines
- [docs/schema-reference.md](docs/schema-reference.md) · [docs/verb-reference.md](docs/verb-reference.md) · [docs/hql-grammar.md](docs/hql-grammar.md)
- [docs/canonical.md](docs/canonical.md) — the object envelope + protocol verbs · [docs/sdk.md](docs/sdk.md)
- [docs/governance-policy.md](docs/governance-policy.md) · [docs/vault.md](docs/vault.md) · [docs/event-bus.md](docs/event-bus.md)
- [docs/dependency-licenses.md](docs/dependency-licenses.md) · [docs/adr/](docs/adr/) — design decisions

## The full stack

HydroMemory is the **open foundation of the HydroCognitive Stack** — a closed-loop cognitive
operating architecture (intent → judgment → planning → action → reflection → integration) built
atop this engine. The open core is everything you need for AI memory; the upper cognitive layers
are a separate commercial product.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
