# ADR-0018: Permission-gated event delivery (the bus reuses `check_access`)

Status: Accepted

## Context

The §9 bus broadcasts lifecycle events that name a droplet (absorbed,
transformed, recalled, frozen, …). A subscriber must never learn about a droplet
it is not permitted to read — otherwise the bus becomes a side channel that leaks
the existence, timing, and routing of memory the subscriber could never recall
directly. The governance layer already owns the single per-message permission
gate (`check_access`) that recall and the verbs use (ADR-0014, ADR-0015); the bus
should not invent a second, divergent policy.

## Decision

The bus **reuses governance `check_access` to gate delivery per-subscriber**.
When an event names a `droplet_id` and the bus was constructed with a `repo`,
`publish` loads the droplet once and, for each matching subscription that carries
an identity, calls `check_access(droplet, identity, AccessContext(), Operation.READ)`;
delivery happens only on an allowed decision (`EventBus._permitted` in
`hydromemory.bus.bus`). The check is **context-free READ**: a bare
`AccessContext()` with no consent/thaw/safe-context asserted. Subscriber
identities are coerced via `_coerce_identity` — `None` means topic-only delivery
(the gate is skipped), an object with a `trust_level` (or an `.identity()`) is
used as-is, and a bare app-id string becomes
`AgentIdentity(name=app_id, trust_level=SESSION)`. A failing gate fails closed
(denies) but is isolated so it never breaks the fan-out.

## Consequences

- The bus enforces exactly the same access policy as recall, with no duplicated
  rules — one gate, reused.
- Because the gate is a **context-free READ**, obligations and consent/thaw flows
  are *not* satisfiable at delivery time: any access that governance only permits
  with thaw or consent is treated as not-yet-permitted for event delivery.
  Concretely, a **glacier (thaw-gated) droplet's events reach no subscriber**
  regardless of the subscriber's trust level, because `check_access` denies a
  frozen-reservoir READ without `thaw_granted`/consent and the bus has no way to
  assert those at publish time. This is a deliberate fail-closed limitation, not
  a bug: glacier memory simply does not surface on the live event stream.
- A subscriber with no identity gets **topic-only** delivery (no droplet gate),
  so anonymous/system subscribers still see topic traffic; callers that need
  gating must attach an identity to the subscription.
