# ADR-0007: `recall_threshold` = phase-base + reservoir-additive table

Status: Accepted

## Context

§5.6's pseudocode gates recall with `if score > recall_threshold(memory.phase,
memory.reservoir)`, but the PRD never specifies the threshold values. The
intent is clear from §5.3/§5.4: deep, frozen, or polluted memory should require a
higher bar to surface than fast, active memory, and slower reservoirs should be
slightly harder to recall from than fast ones.

## Decision

`recall_threshold(phase, reservoir)` returns a **phase-base value plus a
reservoir-additive adjustment**, both documented defaults in `recall.py`:

- `PHASE_THRESHOLD_BASE` rises from `liquid` (0.30) and `rain` (0.32) through
  `groundwater`/`ocean` (0.55) and `ice` (0.70) up to `polluted` (0.95).
- `RESERVOIR_THRESHOLD_ADJ` adds 0.0 for `working_stream`, small amounts for
  surface/cloud, and more for groundwater/ocean/glacier/sacred, up to 0.30 for
  `contaminated`.

Unknown phases/reservoirs fall back to neutral defaults (0.5 / 0.0).

## Consequences

- Frozen (`ice`/glacier) and contaminated memory effectively does not surface
  through ordinary recall, matching §12 Example D and the §16 safety metric.
- Fast, active `liquid`/`working_stream` memory recalls readily.
- The values are tunable constants in one place; they are documented as defaults,
  not derived from the (silent) spec.
