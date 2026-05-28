# ADR-0021: Append-only, hash-chained audit log

Status: Accepted

## Context

The §9 vault must be auditable: the owner needs a tamper-evident record of every
access and every access decision over their memory. A plain audit table is not
enough — rows could be edited, deleted, inserted, or reordered after the fact
without detection. The audit trail therefore needs integrity that does not depend
on trusting the database itself.

## Decision

Record every read/write/query and its `AccessDecision` as an `AuditEntry` in an
**append-only, hash-chained** `audit` table (`hydromemory.vault.audit`). Each row
stores `entry_hash = sha256(prev_hash + canonical(entry))`, where `canonical` is
deterministic JSON (sorted keys, no whitespace) over the entry's fields
(`ts`, `actor`, `app_id`, `operation`, `droplet_id`, `allowed`, `obligations`,
`detail`) and the chain seeds from a fixed genesis hash. `AuditLog.append`
extends the chain on every call; `AuditLog.verify_chain` recomputes the chain
from row 0 and returns `False` on the first inconsistency (a broken `prev_hash`
link or a recomputed hash that does not match the stored one), so any edit,
insertion, deletion, or reordering is detected. `VaultRepository` writes an audit
entry on `upsert`/`get`/`delete`/`query`/`search_similar` (`_audit`), and
`enforce_grant` appends one on each grant-checked access.

## Consequences

- The audit trail is tamper-evident: post-hoc edits/insertions/reordering are
  caught by `verify_chain`. It is *evidence of* tampering, not prevention — an
  attacker who can rewrite the whole table can recompute the chain; integrity
  here means inconsistency is detectable, not impossible.
- **Known gap — the cross-app scope short-circuit is not audited.** In
  `VaultRepository.get` (and `delete`/`add_link`/`remove_link`/`touch_cycle`),
  the `_in_scope` check runs *before* the access/audit gate: an out-of-scope
  `get` returns `None` immediately and **no audit row is written**. So an app
  probing for a droplet id outside its L1 scope produces a silent miss with no
  audit trace, whereas an in-scope denied access *is* audited. The scope filter
  short-circuits ahead of the audit/access path by design (it is "this isn't your
  data" rather than "you were denied"), but it means cross-scope probes are
  invisible in the audit log — a real limitation worth recording.
  **Update — closed by [ADR-0028](0028-audit-out-of-scope-access-attempts.md):**
  the single-id methods now audit an out-of-scope attempt as a denied entry
  (`detail="out of app scope"`) before returning; bulk `query`/`search_similar`
  scope-filtering remains a silent per-row filter (not a targeted probe).
- The audit log lives in the same SQLite connection as the droplets
  (`open_vault_store` builds `AuditLog(backing._conn)`), so audit rows and the
  data they describe share a transaction boundary and a file.
