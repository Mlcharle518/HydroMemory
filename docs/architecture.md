# HydroMemory Architecture

This document describes how the HydroMemory Protocol (the PRD source spec) maps
onto the actual modules in this reference implementation. It is meant to be read
alongside the code: every layer below names the real module(s) that implement it.

For the data model itself (the droplet schema, the state vector, the enums) see
[schema-reference.md](schema-reference.md); for the operations see
[verb-reference.md](verb-reference.md) and [hql-grammar.md](hql-grammar.md); for
the access policy see [governance-policy.md](governance-policy.md). The design
decisions that reconcile the spec with the code are recorded as
[ADRs](adr/README.md) and cross-referenced throughout.

## 1. The hydraulic -> memory mapping (§4)

HydroMemory treats memory as *information moving through state*. The PRD's §4
conceptual model maps water properties to memory functions; the implementation
realizes each one in a specific place:

| Water property | Digital memory function                                  | Where it lives in the code |
| -------------- | -------------------------------------------------------- | -------------------------- |
| Flow           | Memory moves through contexts and associations.          | `Links.associations`, `Verbs.flow`, the `links` table |
| Solvent behavior | Memory absorbs traces from the environment.            | `Verbs.absorb` / `pipeline.process_experience` (context + classification fold into `meta` and `state`) |
| Phase change   | Memory changes format based on conditions.               | `phases.apply_phase_transition` (the §5.4 table) |
| Evaporation    | Abstraction: details reduce, essence remains.            | `Verbs.evaporate` -> `intelligence.Abstractor` |
| Condensation   | Pattern clustering into clouds/themes.                   | `Verbs.condense` |
| Precipitation  | Recall into active awareness or agent context.           | `Verbs.precipitate` / `pipeline.recall_for_agent` |
| Runoff         | Fast associative chains.                                 | `Phase.RIVER` + `Trigger.ASSOCIATION` |
| Infiltration   | Deep storage and identity-level shaping.                 | `Verbs.infiltrate` (toward `Reservoir.GROUNDWATER`) |
| Groundwater    | Latent, durable, slow-moving memory.                     | `Phase.GROUNDWATER` / `Reservoir.GROUNDWATER` |
| Ocean          | Collective or generalized archive.                       | `Reservoir.OCEAN` |
| Freezing       | High-integrity preservation.                             | `Verbs.freeze` -> `Phase.ICE` / `Reservoir.GLACIER` |
| Filtration     | Correction, verification, recontextualization.           | `Verbs.filter` -> `contamination.filter_droplet` |
| Contamination  | Noise, bias, false association, low-confidence inference. | `Verbs.pollute` / `intelligence.ContaminationDetector` (§10.1) |

The cycle itself — `Experience -> Absorption -> Flow -> Evaporation ->
Condensation -> Precipitation -> Runoff/Infiltration -> Reservoir Storage ->
Reactivation -> New Experience` — is expressed as the §5.4 phase chain encoded in
`hydromemory/phases.py` and exercised end-to-end by the §12 examples.

## 2. The 7-layer architecture (§5)

The PRD stacks seven layers. Each maps to concrete modules:

### Layer 1 — Experience capture (§5.1)

Raw input (conversations, documents, events, agent observations) enters as a
plain event dict `{"content": ..., "source": ...}` plus a free-form `context`
dict. This is the argument shape accepted by `Engine.absorb`,
`pipeline.process_experience`, and the CLI/HTTP `absorb` entry points. There is
no separate capture daemon in v1 — capture is the front of the pipeline.

### Layer 2 — Droplet encoding (§5.2)

`hydromemory/schema.py` owns the `Droplet` (the atomic unit) and its `State`
vector. Encoding happens in the pipeline: the content is embedded
(`intelligence.Embedder`), classified (`intelligence.Classifier`), and assembled
into a `Droplet` with a seeded `State`. The schema's `from_dict` is deliberately
alias-tolerant so the spec's own example blobs round-trip (`memory_id` -> `id`,
`charge` -> `emotional_charge`, `scope`/`agent_access` -> permissions); see
[ADR-0001](adr/0001-id-canonical-and-spec-aliases.md) and
[ADR-0004](adr/0004-permissions-superset.md). `type` and `semantic_tags` are
promoted to first-class fields even though §7 omits them
([ADR-0002](adr/0002-type-and-semantic-tags-first-class.md)).

### Layer 3 — Reservoir storage (§5.3)

`hydromemory/reservoirs.py` owns the `Reservoir` enum (eight canonical short ids),
alias normalization for the spec's display names
([ADR-0005](adr/0005-reservoir-canonical-ids-and-aliases.md)), and per-reservoir
behavioral metadata (`speed`, `volatile`, `description`) that the recall scorer
reads. Persistence is in `hydromemory/storage/` (see §4 below). Routing a fresh
droplet to its home reservoir is `pipeline.route_to_reservoir`: contaminated ->
`CONTAMINATED`, highly sensitive/identity-relevant -> `SACRED`, otherwise the
fast `WORKING_STREAM`.

### Layer 4 — Phase transformation (§5.4)

`hydromemory/phases.py` transcribes the §5.4 transition chain into a data-driven
table of frozen `TransitionRule` rows (`Liquid + HEAT -> Vapor`, `Vapor +
SIMILARITY -> Cloud`, ... `Filtered + REINTEGRATION -> Liquid/Groundwater`). Each
rule carries an optional `guard(state, context)` and an additive `effects` dict
applied to the state floats (then clamped to `[0,1]`). Numeric thresholds live in
`PhaseConfig` (documented defaults — [ADR-0009](adr/0009-missing-rule-flag-defaults.md)).
When several triggers fire at once, `apply_phase_transitions` orders them by
`TRIGGER_PRIORITY` so protective transitions (e.g. freeze-to-ICE) win over generic
ones.

### Layer 5 — Triggers / events (§5.5)

`hydromemory/triggers.py` defines two families. **Natural forces** (HEAT,
PRESSURE, GRAVITY, WIND, TERRAIN, SALT, COLD, STORM, FILTRATION, POLLUTION) map
external signals to triggers. **Synthetic triggers** (SIMILARITY, ASSOCIATION,
REPETITION, DENSITY, EXTREME_CHARGE, SAFE_CONTEXT, REINTEGRATION) are emitted by
the engine itself as a droplet matures — they complete the §5.4 chain, which
references conditions that are not raw forces
([ADR-0008](adr/0008-synthetic-transition-triggers.md)). `detect_triggers(droplet,
context)` reads the state floats plus the context dict and returns the fired set.

### Layer 6 — Recall and action (§5.6)

`hydromemory/recall.py` implements the §5.6 recall score:

```
recall_score = semantic_similarity + trigger_similarity + contextual_fit
             + pressure + gravity + phase_accessibility + permission_score
             - depth_resistance - contamination_penalty - privacy_risk
```

Terms derived from the droplet + query context (`trigger_similarity`,
`contextual_fit`, `pressure`, `gravity`, `phase_accessibility`,
`depth_resistance`) are computed here; the governance/embedding terms
(`semantic_similarity`, `permission_score`, `privacy_risk`,
`contamination_penalty`) are passed in by the pipeline. Each term is clamped to
`[0,1]` and weighted by `RecallWeights` (all default `1.0` —
[ADR-0006](adr/0006-recall-weights-default-and-clamp.md)). A candidate survives
when its score exceeds `recall_threshold(phase, reservoir)`, a phase-base plus
reservoir-additive table ([ADR-0007](adr/0007-recall-threshold-table.md)). The
surviving droplet is rendered through one of seven `RecallMode`s
(`select_recall_mode` picks; `format_recall` renders) — literal, pattern,
behavioral, warning, silent, user-visible, reflective.

### Layer 7 — Agent / OS / platform interface (§5.7)

The 15 API verbs (§5.7) are `hydromemory/verbs.py`'s `Verbs` class. The §8 agent
roles are `hydromemory/agents/`. The OS/platform tier (§9) is built in v2 — the
memory event bus, the User-Controlled Memory Vault, and integration levels L1–L4
— see [§9 below](#9-osplatform-integration-built--v2). The machine-readable
protocol envelope (§6) is `hydromemory/protocol.py`.

## 3. The capture and recall pipelines (§14)

`hydromemory/pipeline.py` realizes the PRD §14 pseudocode as two
dependency-injected functions (the `repo`, `intelligence`, `check_access`, and
governance scorers are all parameters, so the pipeline is unit-testable with
fakes).

### Capture: `process_experience(event, user_context, ...)`

The numbered §14 steps, as actually implemented:

1. **Capture + encode** — `content = event["content"]`; embed it via
   `intelligence.embedder.embed`.
2. **Classify** — `intelligence.classifier.classify(content)` yields
   `memory_type`, `importance`, `sensitivity`, `expected_lifespan`. A `State` is
   seeded from any explicit floats on the event plus classification nudges
   (`_estimate_state`), and the `Droplet` is constructed (context + classification
   recorded into `meta`).
3. **Assign phase** — `assign_initial_phase` sets `LIQUID` (the `Experience ->
   Liquid` entry).
4. **Assign reservoir** — an explicit `event["reservoir"]` wins; otherwise
   `route_to_reservoir(droplet, classification.sensitivity)`.
5. **Link to existing memories** — `repo.search_similar(embedding, k=5)` finds
   semantic neighbours (the `related` ids).
6. **Create flow edges** — each related id is appended to
   `droplet.links.associations` and persisted via `repo.add_link`.
7. **Detect triggers** — `detect_triggers(droplet, trigger_ctx)`; the fired set is
   recorded in `meta["triggers"]`.
8. **Transform phase if needed** — `apply_phase_transitions(droplet, triggers,
   ...)` advances the droplet along the §5.4 chain (priority-ordered).
9. **Memory policy review** — `check_access(droplet, agent, access_ctx,
   Operation.MUTATE)` may block the write; the decision is captured. (If
   governance raises `NotImplementedError`, the pipeline default-allows and records
   that review was skipped.)
10. **Store if allowed** — `repo.upsert(droplet)`.

It returns a decision dict: `store`/`stored`, the `droplet` (`to_dict`),
`droplet_id`, `phase`, `reservoir`, the fired `triggers`, the `related` ids, and
the policy `decision`.

### Recall: `recall_for_agent(query, agent, context, ...)`

1. Embed the query (`intelligence.embedder.embed`).
2. `repo.search_similar(embedding, k, candidate_filter=gate)` where `gate`
   admits only droplets with `permission_score > 0` (the permission gate runs
   *inside* the similarity search).
3. For each hit: fetch the droplet, compute `permission_score`, `privacy_risk`,
   and `contamination_penalty = 1 - state.purity`, then `hydro_recall_score(...)`.
4. Keep it only if `score > recall_threshold(phase, reservoir)`.
5. `select_recall_mode` + `format_recall` render a `RecallResult`; recall also
   stamps `repo.touch_cycle(..., recalled=now)` because a recall is itself a cycle
   event.
6. Sort by descending score and return the list.

`Engine.absorb`/`recall`/`hql` (in `hydromemory/engine.py`) are thin wrappers
that inject the concrete repo, intelligence, and governance functions into these
pipeline entry points.

## 4. Pluggable intelligence and storage

### Intelligence (`hydromemory/intelligence/`)

The operations that need real NLP sit behind four small ABCs (`Embedder`,
`Abstractor`, `Classifier`, `ContaminationDetector`) bundled into an
`Intelligence` object. `build_intelligence(config)` selects the backend from
`HYDRO_INTELLIGENCE_BACKEND` (default `stub`):

- **Stub (default, offline, deterministic)** — `StubEmbedder` is a stable
  SHA-256 hashing-trick bag-of-words embedding (identical text -> identical
  vector across processes, so similar texts score higher cosine). `StubAbstractor`
  is heuristic EVAPORATE, `StubClassifier` is keyword heuristics, and
  `StubContaminationDetector` encodes the §10.1 rules. No network, no API key — so
  CI and a laptop both work out of the box.
- **Claude (optional, lazy)** — selected with `backend == "claude"`. `anthropic`
  is imported *lazily inside each method*, so importing the module never requires
  the package and the offline path is never affected. Anthropic has no embeddings
  endpoint, so this backend reuses `StubEmbedder` for vectors and uses Claude only
  for the three text operations. A missing API key raises a clear `RuntimeError`
  *only* when a Claude-backed method is actually called.

See [ADR-0011](adr/0011-stub-first-pluggable-intelligence.md).

### Storage (`hydromemory/storage/`)

`open_store(config)` returns a `SqliteDropletRepository` implementing the
`DropletRepository` contract. It is a **hybrid SQLite store**: the queryable
droplet dimensions (id, content, source, phase, reservoir, memory_type, purity,
visibility, ...) are columns; the rest is JSON; the `links` table is the source of
truth for the droplet graph. Semantic search is backed by a **file-backed
brute-force cosine `VectorIndex`** (numpy): vectors are L2-normalized on insert so
cosine is a single matrix-vector dot product. The index is a *rebuildable cache*
persisted next to the database as `{db_path}.vec.npz`, so reopening recovers both
the rows and the embeddings. This is intentionally exact and simple (no ANN
structures) — the reference impl favors correctness and determinism over scale.
See [ADR-0012](adr/0012-sqlite-plus-vector-index-storage.md).

## 5. The agent runtime / tick model (§8)

The eight §8 roles (Capture, Hydrologist, Archivist, Filtration, Recall, Privacy,
Reflection, Distillation) are implemented in `hydromemory/agents/` as
**synchronous library objects**, not daemons or event-loop subscribers. Each
`BaseAgent` holds an injected, duck-typed `engine` and exposes a single
`run(ctx)` method; it declares the lifecycle `stages` it `handles`.

`AgentRuntime` (`agents/registry.py`) registers agents and `tick(stage, ctx)`
runs each agent that handles `stage`, in registration order, recording each
agent's output into `ctx.results` so later agents in the same tick can read
earlier results. `build_default_runtime(engine)` wires all eight roles in
lifecycle order (capture -> hydrologist -> filtration -> privacy -> recall ->
reflection -> distillation -> archivist). This ordered in-process call is a
faithful, testable stand-in for the future event bus.

## 6. Governance (§10)

`hydromemory/governance/check_access` is the single gate every mutating verb and
recall consults. A decision is the logical AND of (a) the reservoir rule (§10
policy) and (b) the droplet's own `Permissions`. Crucially, **obligations
(explanation / thaw / consent / overwrite-blocked) are *returned*, not
auto-applied** — the calling engine/verb is responsible for satisfying them
before proceeding ([ADR-0015](adr/0015-governance-obligations-returned.md)).
`check_access` denies eagerly only for hard gates (wrong trust level,
filtration-only reservoir, blocked external sharing, glacier without thaw/consent,
sacred overwrite); softer requirements surface as obligations on an *allowed*
decision. The verbs that touch policy (FREEZE/FILTER/POLLUTE/DRAIN/ARCHIVE/FORGET)
**delegate** to the governance/forgetting/contamination modules rather than
re-implementing it ([ADR-0010](adr/0010-verb-co-ownership.md)). Full policy detail
is in [governance-policy.md](governance-policy.md).

## 7. The protocol envelope and the HTTP/TS boundary

`hydromemory/protocol.py` provides `ProtocolEnvelope` / `ProtocolResponse` (the §6
machine-readable shape), keeping the `input`/`classification`/`initial_state`/
`permissions` blocks as plain dicts so every key the spec emits round-trips
losslessly. `hydromemory/server.py` is a FastAPI app over a single, fully-wired
`Engine` (built once at startup, held in `app.state`, closed on shutdown).
Governance is recomputed server-side; `GET /enums` pins the canonical enum
contract. The TypeScript client in `clients/ts` is a thin data shim over this
HTTP/JSON boundary — *not* in-process bindings — with a mirrored type surface
([ADR-0013](adr/0013-typescript-client-over-http.md)).

## 8. Cross-cutting design reconciliations

Several places where this implementation makes a concrete choice the PRD left
open or under-specified are recorded as ADRs. The notable ones:

- One `Phase` enum of all 13 §5.4 values, with `STORABLE_PHASES` = the nine §7
  persisted phases; river/snow/fog/steam are transient-only
  ([ADR-0003](adr/0003-phase-enum-and-storable-subset.md)).
- Synthetic transition conditions modeled as engine-emitted triggers
  ([ADR-0008](adr/0008-synthetic-transition-triggers.md)).
- `Permissions` is the §7 set plus `requires_consent_for_external_use` and
  `requires_user_review` ([ADR-0004](adr/0004-permissions-superset.md)).

See the full [ADR index](adr/README.md).

## 9. OS/platform integration (built — v2)

The PRD §9 envisions HydroMemory at the OS level as a publish/subscribe **memory
event bus** that apps and agents publish to and subscribe from while respecting
permissions, plus a user-owned memory layer with four integration levels. **v2
builds this tier.** What v1 left as three open seams is now realized by three new
module trees — `bus/`, `vault/`, and `platform/` — wired in additively so the v1
behavior is byte-identical when the new pieces are not enabled (the default
emitter is a no-op, the default cipher is plaintext-dev, and the default repo is
the plain SQLite store). For the deep dives see [event-bus.md](event-bus.md),
[vault.md](vault.md), and [integration-levels.md](integration-levels.md); the v2
design decisions are recorded as [ADR-0016 and later](adr/README.md).

### 9.1 The memory event bus (`hydromemory/bus/`)

The bus is the publish/subscribe spine. A `MemoryEvent` (`bus/events.py`) is the
JSON-safe unit published when the lifecycle moves a droplet; its `type` is one of
the canonical `EventType` topics — one per verb effect (`absorbed`, `recalled`,
`frozen`, `filtered`, `forgotten`, ...) plus a generic `transformed`. `EventBus`
(`bus/bus.py`) is **sync at its core** (`publish` is a plain `def`) so the
existing synchronous verbs/pipeline/tests emit without an event loop; it iterates
a snapshot of active subscriptions, so handlers may (un)subscribe during dispatch.
Three properties matter:

- **Permission-gated delivery.** When an event names a `droplet_id` and the bus
  has a `repo`, the droplet is loaded and `check_access(droplet, identity,
  AccessContext(), Operation.READ)` decides delivery *per subscriber* — a
  subscriber never receives an event about a droplet it cannot READ. An anonymous
  subscriber (no identity) gets topic-only delivery; a bare app-id string is
  coerced to a `SESSION`-trust `AgentIdentity`.
- **Error isolation + cascade guard.** A raising handler or predicate never stops
  the fan-out to the remaining subscribers; a nested `publish` beyond `max_depth`
  (default 1) is dropped to prevent event storms.
- **Two handler kinds.** A sync callable runs inline; an `asyncio.Queue`
  (duck-typed via `put_nowait`, drop-oldest when full so `publish` never blocks)
  is the seam the server's WebSocket bridge drains.

`Emitter` (`bus/emit.py`) is the one-line publish helper the engine/verbs hold;
`NULL_EMITTER` (publishing to the no-op `NULL_BUS`) is the default, which is why
v1 stays event-free until a real bus is attached. `NullEventBus` is the
drop-everything default bus.

### 9.2 The User-Controlled Memory Vault (`hydromemory/vault/`)

The vault is an **encrypted, audited, access-enforced, app-scoped** repository.
`VaultRepository` (`vault/vault.py`) implements the same `DropletRepository`
contract by *wrapping* a backing `SqliteDropletRepository` and adding, on every
method: app-scope filtering (L1), `check_access` enforcement, an audit-log entry,
and encryption-at-rest. The encrypt-which-fields split is the crux: routing and
governance columns (phase, reservoir, memory_type, owner, visibility, retention,
external_sharing, purity, app_id) stay **plaintext** so `query` + `check_access`
keep working, while the secrets — content, semantic_tags, the full state vector,
cycle, and meta — are packed into one canonical JSON payload, encrypted to a
single token, and stashed in the on-disk droplet's `meta["__vault__"]`.
Embeddings are stored plaintext (a documented in-process leak) so the vector
index and `rebuild_index` keep working under encryption.

- **Pluggable cipher** (`vault/cipher.py`): `build_cipher` returns a real
  `FernetCipher` when a key is configured (any string works as the key — a raw
  Fernet key is used directly, otherwise a key is derived via SHA-256), else a
  labeled `NullCipher` (plaintext) for offline/dev with a logged warning.
  `cryptography` is a lazy, optional dependency (the `vault` extra), imported only
  inside `FernetCipher`.
- **Tamper-evident audit** (`vault/audit.py`): every read/write/query and every
  access decision is appended to a hash-chained `audit` table
  (`entry_hash = sha256(prev_hash || canonical(entry))`); `verify_chain` detects
  insertion, edits, or reordering.
- **App scope** (`vault/scope.py`): `AppScope(app_id=...)` is the L1 single-app
  view; `AppScope(cross_app=True)` is the L2 owner view across all apps.

`open_vault_store` / `build_vault_engine` (in `vault/__init__.py`) wire a scoped,
encrypted vault into the standard `Engine` (reusing the v1 intelligence + `Verbs`
bundle), so every engine/verb operation becomes encrypted, audited, and scoped.

### 9.3 The platform layer L1–L4 (`hydromemory/platform/`)

The four integration levels map to concrete modules:

| Level | Name                   | Realized by |
| ----- | ---------------------- | ----------- |
| L1    | App Memory             | `platform/apps.py` — `AppMemory` binds an `app_id` to a scoped vault view + bus client + grant store. `absorb` tags the droplet's scope and announces `ABSORBED`; `recall` runs each candidate through `enforce_grant`. `register_app` builds the handle. |
| L2    | User Memory Vault      | `vault/` — the owner's cross-app (`AppScope(cross_app=True)`) `VaultRepository`. A user-proxy identity bypasses the app-grant layer and sees memory across every app scope. |
| L3    | Agentic Memory Mesh    | `platform/mesh.py` — `Mesh` subscribes the §8 roles (and external agents) to the bus as reactions (`ABSORBED`→assess/route, `POLLUTED`→filter, `DISTILLED`→re-verify), each `check_access`-gated, de-conflicted (per-cycle dedupe), no-op-suppressed, and depth-bounded. `platform/runtime.py`'s `MeshEngine` + `build_mesh` wire it end to end. |
| L4    | Sovereign Cognitive OS | `platform/grants.py` — a capability/consent grant protocol. An app files a `GrantRequest`; the owner approves; `enforce_grant` composes `check_access` **AND** an active grant (a pure AND, so a grant can only ever *narrow* governance, never widen it). `GrantStore` persists requests/decisions in a `grants` table with lazy expiry. |

### 9.4 How the three v1 seams were filled

v1 ([ADR-0014](adr/0014-defer-os-bus-and-mesh.md)) deliberately left three seams.
v2 fills each one **additively**, without rewriting the lifecycle/recall/
governance core:

- **`AgentRuntime.tick` → `BusAgentRuntime`.** The synchronous, ordered
  `tick(stage)` loop (`agents/registry.py`, unchanged) gains a bus-driven
  counterpart, `BusAgentRuntime` (`bus/runtime.py`): each §8 agent is *subscribed*
  to the bus topics that correspond to the lifecycle stages it `handles`
  (`STAGE_TOPICS` maps stage → topics), and is invoked on a delivered
  `MemoryEvent` with the event on `ctx.payload["event"]`. `bus_runtime_from_engine`
  mirrors `build_default_runtime` (same eight roles, same order) but wires them as
  subscribers rather than a `tick` loop. The publish/subscribe swap the v1
  docstring promised now exists, and `tick` itself is untouched.
- **`check_access` → bus delivery gate + vault enforcement + `enforce_grant`.**
  The single governance entry point is reused, unchanged, at every new
  cross-boundary point: the bus calls it to gate per-subscriber delivery
  (§9.1), the `VaultRepository` calls it on every CRUD/query method (§9.2), and
  the L4 layer composes it with grants in `enforce_grant` as a pure AND so a grant
  can only narrow it (§9.3). No separate enforcement path was added.
- **`DropletRepository` → `VaultRepository`.** L2 is exactly the v1 prediction
  realized: the repository contract pointed at shared, user-owned, *encrypted*
  storage. Because `Engine`/`Verbs`/pipeline depend only on the abstract
  `DropletRepository`, `VaultRepository` slots in as the repo and the lifecycle,
  recall, and governance code is unchanged — it just transparently gains
  encryption, audit, and app-scoping.

Known limitations are documented in the v2 ADRs ([ADR-0016+](adr/README.md)) and
in [vault.md](vault.md): notably, embeddings and the routing/governance columns
are stored plaintext-at-rest by design (so the vector index and `check_access`
keep working), and the bus is in-process (the WebSocket bridge fans it out to
remote subscribers but there is no cross-process broker).

## 10. Final system map (§17)

The PRD's §17 flow, as realized here:

```
External Triggers
   heat, pressure, gravity, wind, salt            -> triggers.py (natural forces)
        |
Experience Capture -> Memory Droplet Encoder -> Phase Engine
   pipeline.process_experience -> schema.Droplet -> phases.apply_phase_transition(s)
        |
Reservoir Layer
   stream / surface / cloud / groundwater / glacier / ocean / contaminated / sacred
   reservoirs.Reservoir  (persisted via storage/)
        |
Flow Graph
   associations, contradictions, supports, derived_from
   schema.Links  (links table is source of truth)
        |
Recall Engine
   precipitate, thaw, irrigate, distill
   pipeline.recall_for_agent + recall.py + verbs.py
        |
Agent / OS / App Behavior
   verbs.Verbs (15 verbs) + agents/ (§8 roles)
        |
Memory Event Bus (§9, v2)               -> bus/ (EventBus, MemoryEvent, Emitter)
   publish/subscribe, permission-gated      BusAgentRuntime drives §8 roles by topic
        |
OS / Platform Layer (§9, v2)
   L1 App Memory      -> platform/apps.py (AppMemory, register_app)
   L2 User Vault      -> vault/ (encrypted, audited, app-scoped VaultRepository)
   L3 Agentic Mesh    -> platform/mesh.py (Mesh: agents react to bus events)
   L4 Sovereign OS    -> platform/grants.py (GrantStore + enforce_grant)
        |
Reintegration
   filter, deepen, drain, forget
   verbs FILTER / INFILTRATE / DRAIN / FORGET -> contamination/forgetting/governance
```

Core principle: *memory is not stored information; memory is information moving
through state.*
