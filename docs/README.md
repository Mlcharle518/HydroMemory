# HydroMemory Protocol

A reference implementation of the **HydroMemory Protocol** — a stateful memory
architecture with a hydraulic model over a computational memory lifecycle.
Memories are *droplets* with a physical-style **state vector** (temperature,
pressure, gravity, purity, depth, ...) that move through **phases** (liquid,
vapor, cloud, rain, groundwater, ice, ...) and live in **reservoirs** (working
stream, surface, groundwater, glacier, cloud, ocean, contaminated, sacred).
Recall is not plain similarity search: it scores phase accessibility, contextual
fit, permissions, contamination, and privacy together. The core thesis:

> Memory is not stored information; memory is information moving through state.

This repository is a runnable Python core engine plus a thin TypeScript client.
It does **not** claim that water physically stores memories — it uses water
behavior as a rigorous computational model for dynamic continuity, transformation,
and recall.

## Quickstart

Requires Python >= 3.11.

```bash
# from the repository root
python -m venv .venv
# Windows (PowerShell):   .\.venv\Scripts\Activate.ps1
# Windows (git-bash):     source .venv/Scripts/activate
# macOS / Linux:          source .venv/bin/activate

pip install -e ".[dev]"          # core + numpy + test/lint tooling
```

Optional extras (see `pyproject.toml`):

| Extra            | Pulls in                          | When you need it                                    |
| ---------------- | --------------------------------- | --------------------------------------------------- |
| `.[dev]`         | pytest, ruff, mypy, httpx, cryptography, websockets | running tests / linting / type-checking |
| `.[server]`      | fastapi, uvicorn, websockets      | running the HTTP server (`hydromem-server`), incl. the WS bus bridge |
| `.[vault]`       | cryptography                      | real (Fernet) encryption for the v2 memory vault    |
| `.[claude]`      | anthropic                         | the optional Claude intelligence backend            |

You can combine them, e.g. `pip install -e ".[dev,server]"`.

The only hard runtime dependency is `numpy` (used by the vector index). The
default intelligence backend is a deterministic, offline **stub** — no API key,
no network — so the engine runs fully out of the box.

### Run the tests

```bash
pytest                 # 461 tests (v1 core + v2 §9 bus/vault/platform); pyproject sets -q by default
```

## CLI usage

Installing the package exposes a `hydromem` console script (equivalently
`python -m hydromemory.cli`). Global options `--db PATH` (SQLite store; default
`$HYDRO_DB_PATH` or `hydromemory.db`) and `--backend {stub,claude}` (default
`stub`) precede the subcommand.

```bash
# Absorb an experience (full §14 capture pipeline). --context is a JSON object.
hydromem absorb --content "I prefer concise, structured answers about architecture." \
                --context '{"topic":"communication_preference"}'

# stored:    True
# id:        mem_aadc87d9
# phase:     liquid
# reservoir: working_stream
# triggers:  gravity

# Recall memory for a query (ranked RecallResult objects).
hydromem recall "how do I like answers"

# [behavioral] score=1.571 show_to_user=False
#   guidance: Adapt behavior to: 'I prefer concise, structured answers ...' (do not quote the memory).

# Run a Hydro Query Language (HQL) statement.
hydromem hql 'GET memories WHERE reservoir="working_stream"'
hydromem hql 'GET memories WHERE reservoir="groundwater" AND purity>0.8'

# Run one of the six PRD §12 source examples (A–F).
hydromem run-example A

# Vault key management (keys come from the environment, never argv).
# Encrypt a previously-keyless vault under a new key:
HYDRO_VAULT_KEY=<key> hydromem --db mem.db vault-encrypt
# Rotate to a new key (old key stays readable until rotation completes):
HYDRO_VAULT_KEY=<new> HYDRO_VAULT_PREV_KEYS=<old> hydromem --db mem.db vault-rotate
```

Useful flags:

- `absorb`: `--content` (required), `--source` (default `conversation`),
  `--context` (a JSON object string).
- `recall` / `hql`: positional `query`, plus `--agent NAME` (default `assistant`)
  and `--trust {session,approved,high_trust}` (default `approved`).
- `vault-rotate` / `vault-encrypt`: no flags — keys are read from `HYDRO_VAULT_KEY`
  (and `HYDRO_VAULT_PREV_KEYS` for rotation) so secrets never appear in argv. Both
  are owner-only, idempotent, and audited. See [vault.md](vault.md#key-rotation).
- Persist across invocations by passing the same `--db mem.db` each time
  (the absorb above and a later recall must share a database to see each other).

By default everything uses the **stub** intelligence backend. Selecting
`--backend claude` (or `HYDRO_INTELLIGENCE_BACKEND=claude`) switches the three
text operations to Claude and requires the `anthropic` package plus an
`ANTHROPIC_API_KEY`; embeddings still use the deterministic stub embedder
(Anthropic exposes no embeddings endpoint).

When `HYDRO_VAULT_KEY` (or `HYDRO_VAULT_ENABLED`) is set, `absorb` / `recall` /
`hql` automatically run through the encrypted, audited **vault** store (the
owner's cross-app vault) rather than the plain engine — so a keyed `absorb`
encrypts at rest and a keyed `recall` decrypts. With no key set, the CLI behaves
exactly as before (plain engine, no encryption).

### The six §12 examples

`hydromem run-example <A-F>` runs a self-contained scenario on a throwaway demo
engine and prints a short narrative plus the end-state facts. Each demonstrates a
different lifecycle path:

| Example | Title                                  | Demonstrates                                                                 |
| ------- | -------------------------------------- | ---------------------------------------------------------------------------- |
| A       | Meeting dismissal -> cloud recall      | ABSORB -> EVAPORATE -> CONDENSE -> PRECIPITATE: experiences abstract into a cloud pattern that recall surfaces. |
| B       | Preference becomes principle           | A *repeated* preference earns its way down to identity-level GROUNDWATER and recalls as behavioral guidance. |
| C       | Temporary task does not become identity | The over-memory guardrail: a one-off fact stays shallow and is never silently promoted to identity. |
| D       | Sensitive memory gets frozen           | FREEZE -> ICE/GLACIER; transformation is gated behind consent + thaw; recall only in safe contexts. |
| E       | Polluted memory becomes filtered       | POLLUTE -> contaminated pool, then FILTER raises purity and makes it usable again. |
| F       | Conflict resolution                    | Conflicting preferences are reconciled (a `contradictions` link + FILTER), not overwritten. |

## HTTP server and TypeScript client

A small FastAPI JSON boundary exposes the engine for non-Python callers:

```bash
pip install -e ".[server]"            # fastapi + uvicorn + websockets
hydromem-server                       # serves on $HYDRO_HOST:$HYDRO_PORT (default 127.0.0.1:8077)
# or, equivalently:
uvicorn hydromemory.server:app
```

Core endpoints: `GET /healthz`, `GET /enums`, `POST /absorb`, `POST /recall`,
`POST /hql`, `GET /memory/{id}`, `POST /freeze`, `POST /drain`, `POST /forget`.
Governance is recomputed **server-side** from an optional `agent` name and
`trust` level — clients cannot smuggle in a decision. `GET /enums` is the
canonical enum contract the TypeScript client mirrors (it now also pins
`event_types` and `grant_statuses`).

The thin TypeScript client lives in [`clients/ts`](../clients/ts) (`HydroMemoryClient`
over `fetch`, with a full schema/protocol type mirror). It contains no business
logic — all scoring, lifecycle, and governance run on the server. See the
TypeScript-client and HTTP docs (verb/HQL references below) for the request/response
shapes.

## v2: the §9 OS/platform layer (event bus, vault, L1–L4)

v2 builds the PRD §9 tier that v1 deferred: a memory event bus, an encrypted
User-Controlled Memory Vault, and integration levels L1–L4. The deep dives are
[event-bus.md](event-bus.md), [vault.md](vault.md), and
[integration-levels.md](integration-levels.md); the architecture mapping is
[architecture.md §9](architecture.md#9-osplatform-integration-built--v2).

### Running the server with the memory event bus

The bus is wired automatically when you run `hydromem-server`: on startup the
server constructs an `EventBus` over the engine's repo and calls
`engine.attach_bus(...)`, so the verbs + capture/recall pipeline publish lifecycle
events (`absorbed`, `recalled`, `frozen`, ...). Delivery is **permission-gated** —
a subscriber never receives an event about a droplet it cannot READ. The new
bus/platform endpoints (all governance recomputed server-side):

| Endpoint | Purpose |
| -------- | ------- |
| `POST /events` | Publish a `MemoryEvent` onto the bus; returns `{ "delivered": N }` (the subscriber count). The `actor` is the server-computed agent name. |
| `WS /events/subscribe` | Stream live bus events as JSON frames. Query params: `agent` / `trust` (the subscriber identity for the permission gate) and an optional comma-separated `topics` filter. Backed by a bounded queue (drop-oldest when full, so a slow client never blocks publishers). |
| `POST /grants/request` | File an L4 capability/consent grant request (`app_id`, `owner`, `reservoirs`, `operations`, `purpose`, optional `expiry`); returns a `pending` grant. |
| `POST /grants/{id}/approve` · `/deny` · `/revoke` | Owner-only grant transitions (body `{ "owner": "..." }`). |
| `GET /grants?owner=` | List every grant owned by `owner`. |
| `POST /apps` | Register an L1 app-scoped memory handle (`app_id`, optional `owner`). |

In a quick local check, `POST /events` returned `{"delivered": 0}` with no
subscribers, a `WS /events/subscribe?topics=absorbed` client received a
subsequently-published `absorbed` event, and a grant round-tripped
`pending → approved` via the endpoints above.

### Enabling the vault (encryption at rest)

The vault is opt-in. Set `HYDRO_VAULT_KEY` and install the `vault` extra for real
encryption; absent a key the vault falls back to a `NullCipher` dev mode that
stores **plaintext** (and logs a warning) so offline tests need no secrets:

```bash
pip install -e ".[vault]"             # cryptography (Fernet)
export HYDRO_VAULT_KEY="a-strong-passphrase"   # any string works (raw Fernet key, else SHA-256-derived)
export HYDRO_APP_ID="calendar"        # optional: an L1 app scope; omit for the L2 owner (cross-app) vault
```

`build_vault_engine(config, app_id=...)` builds an `Engine` whose repo is a
scoped `VaultRepository`: content, semantic tags, the full state vector, cycle,
and meta are encrypted at rest, while routing/governance columns stay plaintext
so recall and `check_access` keep working. Every operation is also written to a
tamper-evident, hash-chained audit log. (Verified locally: with a key set, the
on-disk `content` column is a Fernet token and the original plaintext is absent.)
The vault is **not** wired into the default `hydromem-server` engine in v2 — it is
used via `build_vault_engine` / `open_vault_store` and the L1/L2 scenario
helpers; see [vault.md](vault.md) for embedding details and known limitations.

### TypeScript client: bus, grants, and apps

`HydroMemoryClient` gains methods mirroring the new endpoints:

- `publishEvent({ type, droplet_id?, app_id?, payload?, agent?, trust? })` →
  `POST /events`, resolves to the delivered count.
- `subscribeEvents({ agent?, trust?, topics? }, onEvent)` → opens the
  `WS /events/subscribe` socket (http(s) base URL rewritten to ws(s)), parses each
  frame into a `MemoryEvent`, and returns a disposer that closes the socket.
- `requestGrant({ appId, owner, reservoirs, operations, purpose, expiry? })`,
  `approveGrant(id, owner)`, `denyGrant(id, owner)`, `revokeGrant(id, owner)`,
  `listGrants(owner)` → the `/grants/*` endpoints.
- `registerApp(appId, owner?)` → `POST /apps`.

### Integration levels L1–L4 (one paragraph)

The four PRD §9 levels are: **L1 App Memory** — an app gets its own scoped vault
view (`AppScope(app_id=...)`) and only sees its own droplets; **L2 User Memory
Vault** — the owner's cross-app view (`AppScope(cross_app=True)`) aggregates
memory across apps, with a user-proxy identity that bypasses the app-grant layer;
**L3 Agentic Memory Mesh** — the §8 agents (and external agents) react to bus
events and propose vault operations, each permission-checked, de-conflicted, and
depth-bounded so events cannot storm; **L4 Sovereign Cognitive OS** — apps request
scoped, time-bounded capability grants that the owner approves, and `enforce_grant`
composes governance **AND** the active grant (a grant can only ever *narrow*
access, never widen it). Full detail and scenarios are in
[integration-levels.md](integration-levels.md).

## Project layout

```
hydromemory/
  __init__.py            package root (import-light)
  config.py              HydroConfig (db path, vector dim, backend selection, vault_key/app_id)
  schema.py              Droplet, State, Phase, Permissions, Links, Cycle (+ from_dict aliases)
  reservoirs.py          Reservoir enum, alias normalization, §5.3 behavioral metadata
  phases.py              §5.4 transition table, guards, PhaseConfig, apply_phase_transition(s)
  triggers.py            §5.5 natural forces + synthetic triggers, detect_triggers
  recall.py              §5.6 hydro_recall_score, recall_threshold, RecallMode, format_recall
  verbs.py               the 15 API verbs (Verbs); co-owned verbs delegate to gov/forget/contam
  protocol.py            §6 ProtocolEnvelope / ProtocolResponse
  pipeline.py            §14 process_experience + recall_for_agent + route_to_reservoir
  engine.py              build_engine(bus=) + Engine facade (absorb/recall/hql/attach_bus/close)
  cli.py                 hydromem CLI (absorb/recall/hql/run-example/vault-rotate/vault-encrypt)
  server.py              FastAPI HTTP boundary (+ hydromem-server entry point; bus/grants/apps)
  intelligence/          pluggable backends: base ABCs, stub (default), claude (lazy)
  storage/               DropletRepository contract, SQLite repo, db schema, vector index
  governance/            §10 check_access, reservoir policy, scoring, obligations, enforcement
  agents/                §8 agent roles + AgentRuntime (the synchronous tick seam)
  bus/                   §9 memory event bus: MemoryEvent, EventBus, Emitter, BusAgentRuntime
  vault/                 §9 User-Controlled Memory Vault: VaultRepository, cipher, audit, scope
  platform/              §9 L1–L4: AppMemory, Mesh, GrantStore/enforce_grant, MeshEngine
  examples/              the six §12 source examples (A–F) + demo harness
clients/ts/              thin TypeScript client over the HTTP/JSON boundary
tests/                   pytest suite (461 tests)
docs/                    this documentation set
```

## Documentation

- [Architecture](architecture.md) — the hydraulic memory model, the 7-layer
  architecture, the capture/recall pipelines, pluggable intelligence + storage,
  the agent runtime, and the §9 OS/platform layer (bus, vault, L1–L4).
- [Closing the gaps](closing-the-gaps.md) — HydroMemory vs. the four hard problems
  of LLM memory (context limits, retrieval, pollution, consolidation): what's
  already solved, the remaining gaps, the frozen spreading-activation spine
  contract, and the phased roadmap (ADRs [0030–0034](adr/README.md)).
- [Efficacy evals design](../evals/README.md) — the harness that *measures* how well
  the gap-closure mechanisms fare against the four problems in practice (baselines,
  metrics, hand-built synthetic datasets first, external benchmark later) —
  efficacy, not correctness. Design only; not yet implemented.
- [Schema reference](schema-reference.md) — the droplet schema, state vector,
  enums, and governance/permissions model. *(authored separately)*
- [Verb reference](verb-reference.md) — the 15 API verbs. *(authored separately)*
- [HQL grammar](hql-grammar.md) — the Hydro Query Language. *(authored separately)*
- [Governance policy](governance-policy.md) — reservoir access policy and
  obligations. *(authored separately)*
- [Event bus](event-bus.md) — the §9 memory event bus: events, topics,
  permission-gated delivery, and the WebSocket bridge. *(authored separately)*
- [Vault](vault.md) — the §9 User-Controlled Memory Vault: encryption at rest,
  the audit chain, and app scoping. *(authored separately)*
- [Integration levels](integration-levels.md) — L1–L4 (App Memory, User Vault,
  Agentic Mesh, Sovereign Cognitive OS). *(authored separately)*
- [Architecture Decision Records](adr/README.md) — the reconciliations between
  the PRD source spec and this implementation (incl. the v2 ADRs 0016+).

## Status / scope

This is a **runnable reference implementation** of the core protocol: the
droplet lifecycle, phase transitions, the full recall scorer, the 15 verbs, HQL,
§10 governance, SQLite + vector-index storage, the six §12 examples, an HTTP
server, and a TypeScript client. **v2 adds the PRD §9 OS/platform layer**: a
publish/subscribe memory event bus, an encrypted User-Controlled Memory Vault,
and integration levels L1–L4 (App Memory, User Vault, Agentic Mesh, Sovereign
Cognitive OS). v1 + v2 together are **461 tests passing** on the offline stub
backend.

The §9 tier is additive: the v1 seams ([ADR-0014](adr/0014-defer-os-bus-and-mesh.md))
were filled without reworking the core — `AgentRuntime.tick` gained a bus-driven
counterpart (`BusAgentRuntime`), `check_access` is reused as the bus delivery gate
and composed with capability grants (`enforce_grant`), and the `DropletRepository`
contract is realized as the encrypted `VaultRepository`. See
[architecture.md §9](architecture.md#9-osplatform-integration-built--v2) for the
full mapping.
