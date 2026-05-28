# ADR-0009: §10 / threshold rule flags get documented defaults

Status: Accepted

## Context

Several PRD rules name a flag or threshold without giving its value or its
default when absent. The §10 reservoir policy rows attach flags like
`user_visible`, `requires_explanation`, `requires_thaw_protocol`,
`usable_for_response`, and `overwrite_allowed` to *some* reservoirs but not
others, leaving the unset cases ambiguous. The §5.4 guards reference numeric
conditions ("density", "extreme charge", "repetition") with no stated cut-offs.
An implementation must pick concrete behavior for every unspecified case.

## Decision

Adopt explicit, documented defaults rather than leaving behavior implicit:

- **Policy flags** default to the conservative/safe reading when a §10 row omits
  them (a reservoir with no `user_visible` is treated as not user-visible; missing
  `overwrite_allowed` is treated as overwrite-blocked for protected reservoirs;
  etc.), defined in `governance/policy.py`.
- **Transition thresholds** are centralized as documented defaults in
  `PhaseConfig` (`density_threshold=0.6`, `extreme_charge_threshold=0.85`,
  `repetition_cycles=3`, `groundwater_gravity_threshold=0.7`) and `TriggerConfig`
  (the per-trigger firing thresholds).

Each default is commented at its definition site as a documented choice, not a
spec value.

## Consequences

- Behavior is total and deterministic for every reservoir and every transition.
- The defaults are tunable in one place and clearly marked as implementation
  choices, so they are not mistaken for normative spec values.
- Safe-by-default policy flags align with the §15 guardrails (do not over-write
  identity/sensitive memory without review).
