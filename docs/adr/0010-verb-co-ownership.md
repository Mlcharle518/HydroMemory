# ADR-0010: Co-owned verbs delegate to governance / forgetting / contamination

Status: Accepted

## Context

Some of the 15 §5.7 verbs are really policy operations wearing a verb's name.
FREEZE and FORGET must run a §10 access review; FILTER and POLLUTE are §10.1
contamination operations; DRAIN and ARCHIVE are §11 forgetting operations
(drainage / sedimentation / sealing). If `Verbs` re-implemented this logic, the
policy would be duplicated across the verb layer and the dedicated
governance/forgetting/contamination modules, and the two copies would drift.

## Decision

The co-owned verbs **delegate** to the dedicated modules rather than
re-implementing policy. `Verbs` receives `check_access`, `forgetting`, and
`contamination` as injected dependencies and calls them:

- `FREEZE` / `FORGET` -> `check_access(...)` (OVERWRITE / MUTATE) before acting,
  and `FORGET` -> `forgetting.delete` + `repo.delete`.
- `FILTER` -> `contamination.filter_droplet`; `POLLUTE` -> `contamination.mark_polluted`.
- `DRAIN` -> `forgetting.drain`; `ARCHIVE` -> `forgetting.sediment` (or
  `forgetting.seal` when `seal=True`).

The verb layer owns orchestration and persistence (`repo.upsert`); the modules
own the policy.

## Consequences

- Policy lives in exactly one place; the verb and the module cannot disagree.
- The verbs are unit-testable with mock modules (the dependencies are injected).
- A verb whose module is not injected raises a clear `RuntimeError` rather than
  silently doing the wrong thing.
- Governance obligations surfaced by `check_access` are handled per
  [ADR-0015](0015-governance-obligations-returned.md) (returned, not auto-applied).
