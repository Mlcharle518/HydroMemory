# ADR-0015: Governance obligations are RETURNED, not auto-applied

Status: Accepted

## Context

The §10 governance model attaches requirements to certain accesses: groundwater
`requires_explanation`, glacier `requires_thaw_protocol` / explicit user consent,
sacred `overwrite_allowed: false`, and external use requiring consent. These
requirements are not all hard denials — many are conditions the caller must
*satisfy* (obtain consent, run a thaw protocol, attach an explanation) before
proceeding. A governance layer could try to satisfy them itself (e.g. auto-thaw,
auto-explain), but that would hide policy effects from the caller and bake
side-effecting behavior into what should be a pure decision.

## Decision

`check_access` returns an `AccessDecision` whose `obligations` list is
**returned, never auto-applied**. The function denies *eagerly* only for hard
gates (wrong trust level, filtration-only reservoir, blocked external sharing,
glacier without thaw/consent, sacred overwrite); softer requirements surface as
`Obligation` values (`REQUIRES_EXPLANATION`, `REQUIRES_THAW`, `REQUIRES_CONSENT`,
`OVERWRITE_BLOCKED`) on an *allowed* decision. The calling engine/verb is
responsible for satisfying each obligation before acting.

## Consequences

- `check_access` stays a pure, side-effect-free decision function — easy to test
  and reason about.
- Callers see exactly what they must do (the obligations) and remain in control of
  consent/thaw/explanation flows, matching §15's "no permanent identity write
  without policy review".
- The §12 Example D flow is expressible: a TRANSFORM on a frozen droplet without
  consent/thaw is denied with `REQUIRES_THAW`/`REQUIRES_CONSENT`, and the same
  call with consent + thaw is permitted.
