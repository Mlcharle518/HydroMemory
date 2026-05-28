# ADR-0008: Synthetic transition conditions modeled as engine-emitted triggers

Status: Accepted

## Context

The §5.5 trigger table lists ten *natural forces* (HEAT, PRESSURE, GRAVITY, WIND,
TERRAIN, SALT, COLD, STORM, FILTRATION, POLLUTION) driven by external signals.
But the §5.4 transition chain references conditions that are **not** in that
table: `Vapor + similarity -> Cloud`, `Cloud + density + trigger -> Rain`, `Rain
+ association -> River`, `River + repetition -> Groundwater`, `Liquid + extreme
charge -> Ice`, `Ice + safe context -> Liquid`, and `Filtered + reintegration ->
Liquid/Groundwater`. "Similarity", "density", "repetition", "extreme charge",
"safe context", and "reintegration" are derived properties of a droplet's state
and lifecycle, not raw external forces.

## Decision

Introduce a second family of **synthetic triggers** — `SIMILARITY`,
`ASSOCIATION`, `REPETITION`, `DENSITY`, `EXTREME_CHARGE`, `SAFE_CONTEXT`,
`REINTEGRATION` — emitted by the engine itself in `detect_triggers` from the
droplet's `State` floats, link structure, cycle count, and context. They are
defined alongside the natural forces in the `Trigger` enum, with
`SYNTHETIC_TRIGGERS = all - NATURAL_FORCES`. The §5.4 table's guards consume them
exactly like natural forces.

## Consequences

- The §5.4 transition chain is implementable verbatim; every arrow has a trigger.
- A clear distinction remains between forces that come from *outside* (natural)
  and conditions the engine *derives* (synthetic), which documents intent.
- Thresholds for the synthetic conditions live in `TriggerConfig`/`PhaseConfig`
  as documented defaults (see ADR-0009).
