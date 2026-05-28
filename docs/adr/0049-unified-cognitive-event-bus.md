# ADR-0049: The unified HydroCognitive event bus

Status: Accepted — implemented (see ../../hydromemory/cognitive_bus/)

## Context

The Master Spec §17 calls for a single **HydroCognitive Event Bus** that routes cognitive objects
across *all* stack layers — memory, intent, judgment, plan, action, reflection, reintegration —
"by type, layer, permission, and owner". What we have today is the §9 *memory* event bus
(`hydromemory/bus/`): excellent, but droplet-centric. Its topics are memory lifecycle verbs
(`absorbed`, `recalled`, …) and its permission gate works by loading the event's **droplet**
through a repo and running `check_access(droplet, identity, …, READ)`. That gate cannot decide an
intent, a plan, or a reflection — it only understands droplets — and no single repo can resolve
every layer's store. So the per-layer bus does not satisfy §17.

The cross-layer primitive already exists: ADR-0047 froze the §8 canonical envelope
(`CanonicalObject`), to which every layer object projects, carrying the routing/gating metadata the
spec names — `object_type`, `owner`, and a `permissions` block (`visibility` ∈ {private, shared,
public}, `allowed_agents`, …). A bus that operates on that envelope can route and gate **uniformly
for all nine object types** without importing a single layer schema.

## Decision

Add a new package `hydromemory/cognitive_bus/` (`events.py` + `bus.py`, re-exported from
`__init__.py`) implementing the unified bus on the canonical envelope. It imports only
`hydromemory.canonical.*` and stdlib — never `hydromemory.hydro*`. Publishers are responsible for
projecting layer objects to a `CanonicalObject` (via `hydromemory.canonical.projection`, built by
another worker) **before** publishing; the bus only ever sees the envelope.

1. **`CognitiveEvent`** — the cross-layer event: `object_ref: CanonicalObject` (the §8 envelope) +
   `verb` (a `CanonicalVerb` value or free string) + `actor` + ISO `timestamp` + `payload`. It has
   `to_dict`/`from_dict` (JSON-safe, mirroring `MemoryEvent`) and `object_type` / `object_id`
   accessors that delegate to the envelope, so subscribers route without reaching into `object_ref`.

2. **`CognitiveBus`** — sync publish/subscribe, the same shape and philosophy as `EventBus`:
   - `subscribe(*, object_types: set[ObjectType] | None, subscriber: str | None, predicate, handler)`
     — register a sync handler; `object_types=None` means all types; `subscriber` is the identity
     string gated against the envelope; `predicate` is an optional extra event filter.
   - `publish(event) -> int` — `publish` snapshots the active subscriptions (so a handler may
     (un)subscribe mid-dispatch), then delivers to each subscription that matches on type **and**
     passes the envelope gate **and** whose predicate returns truthy. Returns the delivered count.
   - `unsubscribe(sub)`.
   - Routing is by `ObjectType` (an enum set) rather than string topics.
   - Handler and predicate errors are isolated — one bad subscriber never stops the fan-out.

3. **Envelope-based permission gate.** The gate is a standalone, separately-testable function
   `envelope_allows(obj, subscriber)`, fail-closed (default DENY):
   - `subscriber is None` (anonymous) → allowed **iff** `visibility == "public"`; anonymous +
     non-public is DENIED (we never leak a private/shared object to an unauthenticated listener);
   - `subscriber == obj.owner` → allowed;
   - `subscriber in permissions.allowed_agents` → allowed;
   - `visibility == "public"` → allowed for everyone;
   - otherwise DENIED. Note `"shared"` is *not* a broadcast — a shared object reaches only its owner
     and its allow-list. Only `"public"` broadcasts.

4. **`NULL_COGNITIVE_BUS`** — a `NullCognitiveBus` that drops everything, the cross-layer analog to
   `NULL_BUS`/`NULL_EMITTER`, usable as a default until a real bus is wired in.

### Why gating moved from droplet-load to the envelope

The memory bus proves a *droplet* READ by loading it and asking governance `check_access`. That is
correct for memory but structurally wrong for a cross-layer bus: (a) it is droplet-only — there is
no droplet for an intent/plan/reflection; (b) it needs a repo, and there is no one repo over all
layers; (c) it would force the bus to import layer machinery, breaking the §17 "route by metadata
alone" contract. The §8 envelope already carries `owner` + `permissions`, the exact inputs a
permission decision needs, for every object type. So the unified bus reads the decision straight off
the envelope — no repo, no per-layer load, no layer import — which is *why* §8 mandates that block.

### Reused from `hydromemory/bus/` vs. added

- **Reused (as a pattern, by import-free mirroring):** the sync-core design — `publish` is a plain
  `def` returning a delivered count, iterating a locked snapshot of active subscriptions; the
  topic/predicate matching structure; per-subscriber error isolation; the JSON-safe
  `to_dict`/`from_dict` event contract; and the `NULL_*` no-op-default convention.
- **Not reused — `bus.Subscription` directly.** Considered, but its shape does not fit cleanly:
  it routes by `topics: frozenset[str]` (we route by `frozenset[ObjectType]`) and its `subscriber`
  is an `AgentIdentity`/app-id coerced through `_coerce_identity` for `check_access` (ours is a
  plain identity **string** gated against the envelope). Forcing it would mean overloading `topics`
  with stringified enums and threading governance identities the envelope gate does not use. Per the
  task's guidance, a minimal local `CognitiveSubscription` is cleaner than bending the memory type.
- **Net-new:** `CognitiveEvent` (envelope-carrying), `CognitiveBus`/`NullCognitiveBus`,
  `CognitiveSubscription`, and the `envelope_allows` gate. No droplet/repo/`check_access`/cascade
  machinery — the unified bus has no re-entrancy guard because, unlike the memory bus, it is not yet
  driving an agent runtime that re-publishes; a guard can be added if/when that lands.

## Consequences

- §17 is satisfied: one bus routes every canonical object type by type + owner + permission, with no
  dependency on any layer schema. The memory bus stays as-is for the droplet-internal §9 lifecycle;
  the two coexist (a future emit seam can mirror memory lifecycle events onto the cognitive bus by
  projecting droplets to envelopes — out of scope here).
- The gate is fail-closed and trivially auditable in isolation (`envelope_allows`), and the design
  is additive/default-off in spirit (`NULL_COGNITIVE_BUS`), consistent with ADR-0025.
- Tests: `tests/test_cognitive_bus.py` covers routing (typed vs. all-types vs. multi-type),
  the gate (owner / allowed_agent / unlisted-private DENY / public broadcast / anonymous fail-closed
  / shared-is-not-broadcast), predicate filtering + error isolation, the event round-trip, and
  `envelope_allows` directly. ruff + mypy clean.
- Follow-ups (out of scope): an `Emitter`-style cognitive emit seam, wiring publishers through
  `to_canonical`, and a cascade guard if the bus later drives re-publishing agents.
