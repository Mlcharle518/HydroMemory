# ADR-0024: L3 mesh = agents as bus subscribers, with cascade safety

Status: Accepted

## Context

The §9 L3 Agentic Memory Mesh has the §8 agents coordinate by reacting to memory
events rather than running in a fixed in-process order. ADR-0014 deferred the
live mesh but fixed `AgentRuntime.tick(stage, ctx)` as the synchronous seam and
promised L3 would be additive at that seam. Two risks make a naive event-driven
mesh dangerous: an event can trigger a reaction that emits another event that
triggers another reaction — an unbounded **cascade / event storm** — and a single
absorbed droplet could be re-processed repeatedly by the same agent.

## Decision

Model the mesh as **§8 agents subscribed to the bus** (`hydromemory.platform.mesh.Mesh`),
driven by a `DEFAULT_REACTIONS` table (ABSORBED→filtration `assess_and_route`,
POLLUTED→filtration `filter`, DISTILLED→reflection `reverify`), each reaction
access-checked under the agent's identity before it applies. The synchronous
`AgentRuntime.tick` is **left untouched** — the mesh is a parallel, bus-driven
path that reuses the same roles (`bus_runtime_from_engine` / `BusAgentRuntime`
mirror `build_default_runtime` without modifying `tick`). Four **cascade-safety**
mechanisms bound it (`Mesh._react`):

- **Depth guard** — a reaction fires only while `payload["_depth"] < max_depth`
  (default 1); every emitted follow-on carries `_depth + 1`.
- **Per-cycle dedupe** — the tuple `(event_type, droplet_id, agent_name)` fires at
  most once per cycle (`_fired`, cleared by `reset_cycle`).
- **No-op suppression** — if the agent's proposed droplet is unchanged
  (`_unchanged`, comparing canonical `to_dict`), nothing is upserted and no
  follow-on event is emitted; the `MeshEngine` transforms a *copy* so a real
  change is detectable.
- **Terminal-phase guard** — FILTERED/ARCHIVED events never trigger reaction, and
  a droplet already in a terminal phase (FILTERED) is never re-reacted to.

A mesh write to the contaminated pool requires a filtration identity: the
reaction is `check_access`-gated under the agent's identity for the reaction's
operation, and denied reactions are skipped and audited.

## Consequences

- L3 is purely additive at the ADR-0014 seam: the v1 synchronous `tick` path is
  unchanged, and the mesh reuses the existing roles as subscribers.
- Cascades are bounded: with `max_depth = 1`, a reaction's follow-on cannot itself
  trigger a third reaction, so an absorbed droplet produces at most a bounded,
  de-duplicated, change-gated chain rather than a storm. Raising `max_depth`
  widens the allowed chain length deliberately.
- Mesh persistence flows through the vault (`Mesh` upserts proposals via the
  `VaultRepository`), so every mesh write is itself encrypted, access-enforced,
  and audited (ADRs 0019, 0021); contaminated-pool writes require the gated
  filtration identity, not an arbitrary agent.
- The dedupe set persists for the lifetime of the mesh's subscriptions unless
  `reset_cycle` is called, so a long-lived mesh treats a repeated
  `(event, droplet, agent)` as already-handled across events until reset.
