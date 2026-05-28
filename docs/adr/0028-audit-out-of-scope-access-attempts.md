# ADR-0028: Audit out-of-scope cross-app access attempts (closes the ADR-0021 gap)

Status: Accepted (amends [ADR-0021](0021-append-only-hash-chained-audit-log.md))

## Context

[ADR-0021](0021-append-only-hash-chained-audit-log.md) records every access
decision in an append-only, hash-chained audit log and noted one **known gap**:
`VaultRepository` checks `_in_scope` *before* the audit/access gate on its
single-id methods, so a cross-app `get` (or `delete`/`touch_cycle`/`add_link`/
`remove_link`) of an out-of-scope id returned `None`/no-op with **no audit row**.
An app probing for droplet ids outside its L1 scope left no trace — a silent
blind spot in a log whose whole purpose is a *complete*, tamper-evident record.
Audit completeness is a headline guarantee of the vault (HydroMemory's
differentiator), so the blind spot is worth closing.

## Decision

On the single-id methods (`get`, `delete`, `touch_cycle`, `add_link`,
`remove_link`), when `_in_scope` fails, append a **denied** audit entry before
returning — via `VaultRepository._audit_out_of_scope(operation, droplet_id)`,
which records `allowed=False` with `detail="out of app scope"` and the natural
operation (`READ` for `get`, `OVERWRITE` for `delete`, `MUTATE` for the cycle/link
mutations). Return values are unchanged: the attempt is still fully isolated
(`None` / no-op), only now it is *visible* in the owner's log.

Bulk `query` / `search_similar` scope-filtering is deliberately left as a **silent
per-row filter** — those are not targeted id probes, and auditing every
scope-skipped row would flood the log without adding signal.

## Consequences

- **The gap ADR-0021 noted is closed for targeted access:** a cross-scope probe of
  a specific id is now a denied audit entry, so `verify_chain`-protected
  completeness holds for the single-id paths. Asserted by
  `tests/test_l1_app_scoping.py::test_l1_out_of_scope_attempt_is_audited`.
- **No cross-app existence leak to the prober.** The attempted `droplet_id` is
  written only to the owner's audit log (apps do not read the audit log), and the
  caller still receives `None` whether or not the id exists — so this records the
  attempt without telling the probing app anything new.
- **Scope is unchanged elsewhere:** isolation behavior, `check_access`, and the
  in-scope denial path are untouched; this only adds audit rows on the
  previously-silent out-of-scope branches. Additive; v1 + v2 stay green.
