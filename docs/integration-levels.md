# Integration Levels L1–L4 (§9)

The §9 OS/platform layer composes the [memory event bus](event-bus.md) and the
[User-Controlled Memory Vault](vault.md) into four progressively-broader
integration levels:

| Level | Name                  | Core idea                                                              |
| ----- | --------------------- | --------------------------------------------------------------------- |
| **L1** | App Memory            | An app sees only its own droplets (per-app scope over a shared vault). |
| **L2** | User Memory Vault     | The owner crosses every app scope and reads the union.                |
| **L3** | Agentic Memory Mesh   | Agents react to bus events and propose vault ops, with cascade safety. |
| **L4** | Sovereign Cognitive OS | Apps request scoped, owner-approved capability grants; enforced on every access. |

The code lives in [`hydromemory/platform/`](../hydromemory/platform)
(`apps.py`, `mesh.py`, `grants.py`, `runtime.py`) plus the vault's
`scope.py`. Each level has an **executable spec** — a scenario test file
(`tests/test_l1..l4.py`) referenced in its section below.

---

## L1 — App Memory

**Goal:** an application absorbs and recalls only within its own scope.

L1 is enforced by `AppScope` (`hydromemory/vault/scope.py`) and the
`VaultRepository`'s `app_id` column:

- `AppScope(app_id="calendar")` — a scoped view; the vault filters every read to
  rows whose `app_id` column matches, and `upsert` stamps new rows with that
  `app_id`.
- An `AppIdentity` binds an `app_id` to an `owner` (default `"user"`).

`build_app_views(backing, cipher, audit, *, app_ids=[...])`
(`hydromemory/platform/runtime.py`) builds per-app L1 views **plus** an L2 owner
view over **one shared backing**. Sharing a single `SqliteDropletRepository`
(hence one SQLite connection and one in-process vector index) means a write
through any app view is immediately visible to the owner view and the vector
index never goes stale across scopes — the trap that opening a fresh store per
app would hit. It returns `(app_views: dict[str, VaultRepository], owner_view:
VaultRepository)`.

The `AppMemory` handle (`apps.py`, via `register_app(engine, app_id, owner)`)
is the app-facing surface: `absorb` tags the droplet's `meta["app_id"]`, persists
it through the scoped vault, and publishes an `ABSORBED` event (which is what
wakes the L3 mesh); `recall` runs each candidate through L4 `enforce_grant`;
`request_access` files an L4 grant request.

**The L1 guarantee:** each app sees only its own droplets via `query` /
`all_ids` / `get`; a cross-app `get` returns `None`. Note (per the
[vault audit gap](vault.md#known-gap-out-of-scope-cross-app-get-is-not-audited))
that the cross-app `get` is *silently* isolated — no audit row — because the
scope check precedes the audit gate.

**Executable spec:** [`tests/test_l1_app_scoping.py`](../tests/test_l1_app_scoping.py).

---

## L2 — User Memory Vault

**Goal:** the owner, operating their own vault, reads across **all** app scopes.

L2 is the cross-app view: `AppScope(cross_app=True)` under a **user-proxy**
identity (`AgentIdentity(name="user", trust_level=HIGH_TRUST,
is_user_proxy=True)`). This is the `owner_view` returned by `build_app_views`,
and the default scope `open_vault_store` / `build_vault_engine` pick when no
`app_id` is configured.

Two properties define L2:

- **Cross-scope aggregation.** The owner view's `_scoped_ids()` returns `None`
  (no filter), so `query` / `all_ids` / `get` see the union of every app's
  droplets, and content decrypts across scopes.
- **The user-proxy clears governance floors.** A user-proxy identity is always
  admitted past per-droplet `allowed_agents`, and HIGH_TRUST clears every
  reservoir's trust floor — so the owner reads higher-trust reservoirs
  (groundwater, sacred) that an individual app could not. Access is still routed
  through `check_access` (the owner is not above governance; they simply satisfy
  it). Sacred is allowed for a user-proxy without explicit consent; glacier still
  requires the thaw protocol.

Because the owner view is cross-app, its own writes are **not** stamped with an
`app_id`; they remain visible to the owner (cross-app sees all) but no single app
scope claims them.

**Executable spec:** [`tests/test_l2_user_vault.py`](../tests/test_l2_user_vault.py).

---

## L3 — Agentic Memory Mesh

**Goal:** agents coordinate via the shared bus — reacting to events and proposing
vault operations — without an orchestrator and without storming.

The `Mesh` (`hydromemory/platform/mesh.py`) wraps the §8 agent roles as bus
subscribers. It builds on the bus + the synchronous `AgentRuntime` **without**
modifying `tick` (the v1 path stays intact). `build_mesh(vault, bus,
intelligence, audit=None, *, max_depth=1)` assembles a `MeshEngine`, a default
runtime bound to it, and the `Mesh`; the caller calls `mesh.attach()` to
subscribe the reactions.

### The reaction table

`DEFAULT_REACTIONS` binds three event topics to a §8 role, the governance
operation the proposal is checked under, and the follow-on event it emits:

| Trigger event | Role         | Action (`MeshEngine`)            | Operation   | Emits         |
| ------------- | ------------ | -------------------------------- | ----------- | ------------- |
| `ABSORBED`    | `filtration` | `assess_and_route(droplet, {})`  | `MUTATE`    | `TRANSFORMED` |
| `POLLUTED`    | `filtration` | `filter(droplet)`                | `TRANSFORM` | `FILTERED`    |
| `DISTILLED`   | `reflection` | `reverify(droplet)`              | `MUTATE`    | `TRANSFORMED` |

So: a freshly **absorbed** droplet is assessed and (if the detector flags it)
routed to the contaminated pool; a **polluted** droplet is filtered into a usable
`FILTERED` droplet; a **distilled** droplet is re-verified.

Each reaction: load the droplet via the vault, `check_access` for the reaction's
operation under the agent's identity (SKIP + audit on deny), apply the agent's
proposed op, and — only if the proposal **actually changed** the droplet — upsert
it and emit the follow-on event carrying `_depth + 1`.

### Mesh writes need a filtration identity

This is the load-bearing wiring fact. The mesh persists the routed droplet via
`vault.upsert`, which gates on the **vault's own** identity. Routing sends a
droplet to the `contaminated` reservoir, which is `filtration_agent_only` in
governance — so a *user-proxy* vault's upsert of it would be **denied** and the
route silently lost. The vault the mesh writes through is therefore opened under
a HIGH_TRUST `is_filtration=True` identity. (READ of the working_stream /
contaminated droplets under that identity is also allowed, so the bus permission
gate delivers the events to the filtration subscriber in the first place.)

### Cascade safety

The mesh has four independent guards against event storms:

- **Depth guard.** A reaction only fires while the event's `payload["_depth"] <
  max_depth`; every emitted follow-on carries `_depth + 1`. With the default
  `max_depth=1`, an `ABSORBED` at depth 0 produces exactly one `TRANSFORMED` at
  depth 1, which does **not** re-trigger a reaction. (This payload-counted guard
  is the mesh's own; it is distinct from the
  [bus's dispatch-depth guard](event-bus.md#cascade-re-entrancy-guard).)
- **Per-cycle dedupe.** The tuple `(event_type, droplet_id, agent_name)` fires at
  most once for the lifetime of the mesh's subscriptions (cleared by
  `reset_cycle()`).
- **No-op suppression.** If the agent's proposed droplet is unchanged (compared
  via `to_dict`), nothing is upserted and no follow-on is emitted. This is why
  `MeshEngine` operates on a *copy* and returns a distinct instance — the mesh's
  `_unchanged` treats a returned-same-instance (`before is after`) as a no-op, so
  the contamination helpers (which mutate-and-return the same object) would
  otherwise be misread as no change.
- **Terminal phases / events.** A droplet already in a terminal phase
  (`FILTERED`) is never re-reacted to, and terminal events (`FILTERED`,
  `ARCHIVED`) never trigger a reaction.

**Executable spec:** [`tests/test_l3_mesh.py`](../tests/test_l3_mesh.py).

---

## L4 — Sovereign Cognitive OS

**Goal:** an app/platform requests scoped access to the user's memory; the owner
approves; an approved grant is enforced on **every** access — and can only ever
**narrow** the base governance decision, never widen it.

### The grant lifecycle

`grants.py` defines the types and store:

- **`GrantRequest`** — `app_id`, `owner`, `reservoirs`, `operations`, `purpose`,
  optional `expiry`, and a generated `request_id`.
- **`Grant`** — the persisted request plus a `status` and `granted_at`.
- **`GrantStatus`** — `pending`, `approved`, `denied`, `revoked`, `expired`.
- **`GrantStore`** — persists grants in the `grants` table (`GRANTS_DDL`). The
  flow is `request(req)` (-> `PENDING`) then the owner-only transitions
  `approve` (-> `APPROVED`, stamps `granted_at`), `deny`, `revoke`. The
  owner-only transitions raise `PermissionError` if a different owner attempts
  them.

`active_for(app_id)` returns only `APPROVED`, non-expired grants. **Expiry is
evaluated lazily at read time** against the wall clock: a grant whose `expiry` is
in the past is treated as inactive (excluded) without the stored `status` being
rewritten — there is no `EXPIRED`-writing background job, so the `expired` status
value is effectively a logical state, not a stored one in this path.

### enforce_grant — check_access AND an active grant (narrow-only)

```python
enforce_grant(droplet, agent, context, operation, *, app_id, store, audit=None) -> AccessDecision
```

The composition is a pure AND — reservoir policy ∧ droplet permissions ∧ grant:

1. **`check_access` first.** If the base governance decision denies, it is
   returned **unchanged** — a grant can never resurrect a governance denial.
2. **User-proxy bypass (L2).** If `agent.is_user_proxy` (the owner acting
   directly), the base decision is returned: the owner bypasses the app-grant
   layer entirely.
3. **Otherwise require an active grant** for `(app_id, droplet.owner)` whose
   `reservoirs` contains the droplet's reservoir **and** whose `operations`
   contains `operation`. A missing `app_id`, or no matching grant, yields a DENY.
4. **Audit.** On the allow-after-grant path and on the deny paths, an audit entry
   is appended (when an `audit` log is provided).

Because step 1 runs first and step 3 only ever turns an allow into a deny,
`enforce_grant` is strictly narrowing.

### Known gap: the user-proxy bypass is not audited

Step 2 returns the base decision **before** the audit call, so the owner's
allowed access is intentionally **not** recorded by `enforce_grant`. The
rationale: that is the owner operating their own vault at L2, audited there
instead. This is asserted by
`tests/test_l4_grants.py::test_l4_owner_user_proxy_bypasses_grant` (which checks
`audit.query(actor="owner") == []`) and documented in that file's module
docstring.

### Server surface

The reference server (`hydromemory/server.py`) exposes the L4 protocol; the
`GrantStore` shares the engine's SQLite connection:

| Endpoint                        | Purpose                                          |
| ------------------------------- | ------------------------------------------------ |
| `POST /grants/request`          | File a grant request (-> `PENDING`).             |
| `POST /grants/{request_id}/approve` | Owner-only: approve (body `{"owner": ...}`).  |
| `POST /grants/{request_id}/deny`    | Owner-only: deny.                            |
| `POST /grants/{request_id}/revoke`  | Owner-only: revoke.                          |
| `GET /grants?owner=...`         | List every grant for an owner.                   |
| `POST /apps`                    | Register an L1 `AppMemory` handle wired to the server's bus + grant store. |

Store errors map to HTTP codes: a missing request -> `404`, a wrong-owner
transition -> `403`. The TS client mirrors all of these
(`requestGrant` / `approveGrant` / `denyGrant` / `revokeGrant` / `listGrants` /
`registerApp`), and the `GrantStatus` union in `clients/ts/src/types.ts` mirrors
the five Python status values.

**Executable spec:** [`tests/test_l4_grants.py`](../tests/test_l4_grants.py).

---

## How the levels compose

A single end-to-end picture: an **L1** app absorbs a droplet (scoped, tagged,
encrypted in the [vault](vault.md)) and announces it on the
[bus](event-bus.md). The **L3** mesh's filtration reaction wakes on that
`ABSORBED` event, assesses the droplet, and — writing through a filtration-trust
vault — routes it if contaminated, emitting a single bounded follow-on. The
**L2** owner, on their cross-app view, later reads that droplet (and every other
app's) subject to governance. When a *different* app wants in, **L4** requires the
owner to approve a scoped capability grant, and `enforce_grant` narrows every one
of that app's accesses to exactly what was granted.
