# ADR-0025: Additive layering — v2 is new modules + no-op-default seams; v1 stays green

Status: Accepted

## Context

v2 adds the entire §9 OS/platform layer — the event bus, the encrypted/audited
vault, and the L1–L4 integration (apps, mesh, grants). The risk in a change this
large is regressing the validated v1 core: the lifecycle, recall, governance,
forgetting, contamination, storage, HQL, and the §12 acceptance examples that
276 v1 tests pin. ADR-0014 promised L1–L4 would arrive as an additive change at
known seams (`tick`, `check_access`, `DropletRepository`) rather than a rewrite.

## Decision

Implement all of v2 as **new modules plus seams that default to no-ops**, so the
v1 code paths are unchanged unless a v2 component is explicitly wired in:

- The bus, vault, and platform are new packages (`hydromemory/bus`,
  `hydromemory/vault`, `hydromemory/platform`); nothing in the v1 modules depends
  on them being active.
- Emission defaults to `NULL_EMITTER`/`NULL_BUS` everywhere (the verbs, the
  pipeline, the engine) so v1 behavior is byte-identical until `attach_bus`
  (ADR-0017).
- The vault is a `DropletRepository` wrapper reached only via `build_vault_engine`
  / `open_vault_store`; the default engine still uses the plain
  `SqliteDropletRepository` (ADRs 0019, 0022).
- The mesh is a parallel bus-driven runtime that does not modify `AgentRuntime.tick`
  (ADR-0024); the schema change is the additively-migrated nullable `app_id`
  column (ADR-0022).

## Consequences

- The v1 suite stayed green throughout: the test count grew from **276 to 386**
  (an additive +110 across the bus, vault, L1–L4 scenario, and v2 server suites:
  `tests/test_v2_bus.py`, `test_v2_vault.py`, `test_v2_platform.py`,
  `test_l1_app_scoping.py`, `test_l2_user_vault.py`, `test_l3_mesh.py`,
  `test_l4_grants.py`, `test_v2_server.py`), with no v1 test modified.
- The v2 layer is opt-in and removable: a caller that never wires a bus, never
  builds a vault engine, and never attaches the mesh gets exactly the v1 system.
- Each v2 decision lives behind a documented seam, so the layer can be replaced
  piecewise (e.g. a broker-backed bus per ADR-0016, an encrypted-vector index per
  ADR-0020) without touching the core.
