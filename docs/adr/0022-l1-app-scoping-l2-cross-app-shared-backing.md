# ADR-0022: L1 app scoping via an `app_id` column; L2 owner vault over one shared backing

Status: Accepted

## Context

The §9 integration levels need two views of the same vault: **L1 App Memory**,
where an app sees only its own droplets, and **L2 User Memory Vault**, the
owner's cross-app view that aggregates memory across apps. The naive way to give
each app its own view is to open a fresh store (a new `SqliteDropletRepository`)
per app — but ADR-0012's vector index is a per-instance, file-backed cache, so
multiple instances over the same DB would each hold their *own* index and go
stale relative to one another the moment one app writes.

## Decision

Scope L1 with an **`app_id` column** and build all the L1/L2 views over **one
shared backing repository**:

- The `droplets` table gained a nullable `app_id` column, additively migrated for
  pre-v2 databases via `ALTER TABLE ... ADD COLUMN` when absent
  (`hydromemory.storage.db.init_schema`), with an index on it. The vault stamps
  it with a direct `UPDATE` after upsert (`VaultRepository._tag_app_id`), since
  the backing upsert never sets that column.
- `AppScope` (`hydromemory.vault.scope`) selects the view: `AppScope(app_id=...)`
  is an **L1** scope (the vault filters to rows with that `app_id` via
  `_scoped_ids` / `_in_scope`), and `AppScope(cross_app=True)` is the **L2**
  owner vault (no app filter — it aggregates all scopes), still gated by
  governance on every access.
- `build_app_views` (`hydromemory.platform.runtime`) constructs the per-app L1
  `VaultRepository` views **and** the L2 owner view over the *same* injected
  `SqliteDropletRepository` — one SQLite connection and one in-process vector
  index shared across every scope.

## Consequences

- Writes through any app view are immediately visible to the owner view and to
  every other app view's similarity search, and the vector index never goes stale
  across scopes — the multi-connection / stale-index trap of "a fresh store per
  app" is avoided.
- An existing v1 database opens unchanged: the `app_id` migration is additive and
  nullable, so pre-v2 rows simply carry a `NULL` app_id (and the L2 cross-app view
  sees them).
- App isolation is enforced in the vault layer (the scope filter), not by the
  database — all apps share one file and one connection, which is fine for the
  single-process reference implementation but is not a multi-tenant security
  boundary on its own (governance + the grant layer, ADR-0023, provide the access
  boundary).
- `app_id` is one of the plaintext routing columns (ADR-0019), so which app owns
  a droplet is visible at rest.
