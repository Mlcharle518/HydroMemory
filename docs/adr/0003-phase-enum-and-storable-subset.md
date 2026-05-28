# ADR-0003: One `Phase` enum of 13; `STORABLE_PHASES` is the §7 nine

Status: Accepted

## Context

The PRD uses two different phase lists. §5.4 (the phase transformation layer)
defines thirteen phases including `river`, `snow`, `fog`, and `steam`. But the §7
minimum schema's `phase` field enumerates only nine: `liquid | vapor | cloud |
rain | groundwater | ice | ocean | polluted | filtered`. The four extra §5.4
phases describe *transient* recall/lifecycle states (associative flow chains,
soft preservation, ambiguous recall, high-energy active abstraction) that are not
meant to be persisted as a droplet's resting state.

## Decision

Model a single `Phase` enum with all 13 §5.4 values, and define
`STORABLE_PHASES` as the frozenset of the nine §7 phases.
`TRANSIENT_PHASES = all - STORABLE_PHASES` is therefore exactly
`{river, snow, fog, steam}`. The transient phases are still first-class enum
members (the recall scorer assigns them `phase_accessibility` and threshold
values, since they appear *during* recall) — they are simply not a droplet's
persisted resting phase.

## Consequences

- One enum keeps `phases.py`, `recall.py`, and the §5.4 transition table
  consistent; no parallel "storable phase" type is needed.
- `STORABLE_PHASES` is exported and surfaced in `GET /enums` (as
  `storable_phases`) so the TS client and tests can pin the §7 subset.
- Code that persists a phase can assert membership in `STORABLE_PHASES`; transient
  phases are produced and consumed within a single recall/transition pass.
