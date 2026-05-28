# ADR-0005: Reservoir canonical short ids + alias map

Status: Accepted

## Context

The PRD refers to reservoirs under several display names across §5.3, §6, §10,
and §17. For example the §5.3 table reads "Surface Reservoir", "Cloud Layer",
"Contaminated Pool", and "Sacred Spring", while §6's envelope uses
`surface_reservoir`, §10's policy uses `contaminated_pool` and `sacred_spring`,
and §17's map uses the short forms `stream`, `surface`, `cloud`, `contaminated`,
`sacred`. A single canonical identifier is needed for storage columns, enum
values, and the JSON contract.

## Decision

`Reservoir` is an enum of eight canonical short ids: `working_stream`, `surface`,
`groundwater`, `glacier`, `cloud`, `ocean`, `contaminated`, `sacred`. A
`RESERVOIR_ALIASES` map plus `normalize_reservoir(value)` translates the spec's
display names to canonical (e.g. `surface_reservoir` -> `surface`, `cloud_layer`
-> `cloud`, `contaminated_pool` -> `contaminated`, `sacred_spring` -> `sacred`,
`stream`/`working` -> `working_stream`). All ingest paths (`from_dict`, the
pipeline, the verbs) normalize through this function.

## Consequences

- Every spec spelling resolves to one canonical value, so storage, querying,
  recall, and the HTTP/TS contract use a single vocabulary.
- New display-name aliases are a one-line addition to the map.
- The §10 access policy and the §5.3 behavioral metadata both key off the
  canonical enum, keeping policy and behavior aligned.
