# ADR-0014: Defer the §9 OS memory bus + live agent mesh; leave seams open

Status: Accepted

## Context

The PRD §9 describes HydroMemory at the OS level as a publish/subscribe **memory
event bus** with four integration levels (L1 App Memory, L2 User Memory Vault, L3
Agentic Memory Mesh, L4 Sovereign Cognitive OS) — apps and agents publishing and
subscribing to memory events while respecting permissions. Building a real event
bus, a cross-app vault service, and a live multi-agent mesh is a large systems
effort that is orthogonal to validating the core memory model, and would dwarf the
rest of the reference implementation.

## Decision

**Defer the §9 OS bus and the live multi-agent mesh from v1**, but implement the
core so the seams are already in the right places:

- The §8 agent roles ship as synchronous, in-process library objects, and
  `AgentRuntime.tick(stage, ctx)` runs them in registration order. `tick` is the
  exact boundary where a future runtime would *publish* stage events and let
  agents *subscribe* (with the same per-agent permission checks). The module
  docstring states this explicitly.
- `governance.check_access` is the per-message permission gate that any
  cross-boundary mesh message would pass through — the same gate recall and the
  verbs already use.
- The `DropletRepository` contract is the future L2 vault store: pointing it at
  shared, user-owned storage realizes a central vault without changing the engine.

## Consequences

- v1 is a complete, testable core (276 tests) without the systems weight of a
  live bus/mesh.
- Adding L1–L4 later is an additive change at known seams (`tick`, `check_access`,
  `DropletRepository`) rather than a rewrite of the lifecycle/recall/governance.
- The deferral is documented in `agents/registry.py` and
  [architecture.md](../architecture.md#9-osplatform-integration-built--v2) so the
  open seams are discoverable.
- **Update (v2):** §9 has since been built additively at exactly these seams —
  see ADR-0016 through ADR-0025 and [integration-levels.md](../integration-levels.md).
