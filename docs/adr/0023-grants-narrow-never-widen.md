# ADR-0023: Capability grants narrow, never widen (and the user-proxy bypass)

Status: Accepted

## Context

The §9 L4 Sovereign Cognitive OS lets an app **request** scoped access to the
user's memory, which the owner approves into a capability `Grant`. The danger is
that a grant layer added *on top of* governance could be read as an alternative
authorization path — an app waving an approved grant to reach a droplet that the
reservoir policy or the droplet's own permissions would deny. A grant must be a
restriction the owner places on an app, never an escalation around governance.

## Decision

`enforce_grant` (`hydromemory.platform.grants`) composes governance and the grant
as a pure AND — reservoir policy ∧ droplet permissions ∧ grant — so a grant can
only ever **narrow** the base decision (flip allow→deny), never widen it:

1. Call `check_access` **first**. If governance denies, return that decision
   unchanged — a grant cannot resurrect a governance denial.
2. If `agent.is_user_proxy` (the owner acting directly — L2), return the base
   decision: the owner **bypasses the app-grant layer entirely**.
3. Otherwise require an active, non-expired, owner-approved `Grant` for
   `(app_id, droplet owner)` whose `reservoirs` contains the droplet's reservoir
   and whose `operations` contains the operation. If none matches, return a
   DENY (the base allow is narrowed to a denial).

Grant state lives in a `GrantStore` (`grants` table) with owner-only
`request`/`approve`/`deny`/`revoke` transitions and lazy expiry evaluated at read
time (`active_for`).

## Consequences

- The grant layer is provably non-escalating: it is a logical AND over the
  governance decision, so the worst an app's grant can do is fail to cover an
  access (deny). Apps cannot use grants to reach memory governance forbids.
- The owner (user-proxy) path skips grant enforcement so the user always reaches
  their own vault directly (the L2 view). **Known gap — that owner bypass is not
  audited:** when `agent.is_user_proxy`, `enforce_grant` returns the base decision
  *before* calling its `_audit` helper, so an owner/user-proxy access through the
  grant path produces **no `enforce_grant` audit entry** (a non-owner allowed
  access *does* get one). Owner-direct vault operations are still audited by
  `VaultRepository` itself (ADR-0021); it is specifically the grant-layer audit
  that the bypass skips.
- Expiry is evaluated lazily against the wall clock in `active_for` (a past-expiry
  grant is treated as inactive without rewriting its stored status), so an expired
  grant stops authorizing immediately but its row still reads as APPROVED until an
  explicit transition.
