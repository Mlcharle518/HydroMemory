# ADR-0004: `Permissions` = §7 set + `requires_consent_for_external_use` + `requires_user_review`

Status: Accepted

## Context

The §7 minimum schema's `permissions` block lists `owner`, `visibility`,
`allowed_agents`, `retention`, and `external_sharing`. But other parts of the PRD
require two more flags: §5.2's example droplet sets
`requires_consent_for_external_use: true`, and §6's envelope sets
`requires_user_review: false`. The §10 governance model and the §10.1
contamination block also lean on consent/review semantics. Without these fields,
those spec examples could not be represented and the governance scorers would
have nothing to read.

## Decision

`Permissions` is the §7 set plus `requires_consent_for_external_use: bool` and
`requires_user_review: bool` (both default `False`). They are accepted by
`from_dict`, emitted by `to_dict`, and read by governance: `privacy_risk` adds a
penalty when `requires_consent_for_external_use` is set, and the obligations
machinery uses consent semantics for external use and glacier access.

## Consequences

- The §5.2 and §6 example payloads represent exactly.
- `privacy_risk` and `check_access` have explicit signals for consent-gated
  external use, rather than inferring them.
- The permission model is a compatible superset of §7; §7-only consumers are
  unaffected.
