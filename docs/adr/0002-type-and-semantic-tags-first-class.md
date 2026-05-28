# ADR-0002: `type` and `semantic_tags` promoted to first-class fields

Status: Accepted

## Context

The §7 "minimum memory schema" omits both a memory type and semantic tags. Yet
the rest of the PRD relies on them heavily: the §6 protocol envelope emits a
`classification.memory_type`; §5.2's example droplet carries both `type` and a
`semantic_tags` array; HQL (§13) filters on `type` (`WHERE type =
"communication_preference"`); and the recall scorer's `contextual_fit` needs tags
to match a query's topic. Treating these as opaque `meta` would make them
second-class and awkward to query.

## Decision

`Droplet` carries `memory_type: str | None` and `semantic_tags: list[str]` as
first-class dataclass fields. `from_dict` accepts `type` as an alias for
`memory_type`, and derives `semantic_tags` from `semantic_tags`, then `tags`,
then a list-valued `context` (in that precedence). Both are emitted by `to_dict`
and surfaced in storage so HQL and recall can use them directly.

## Consequences

- HQL `WHERE type = ...` and recall `contextual_fit` work against real,
  queryable fields rather than reaching into `meta`.
- The implementation is a (compatible) superset of the §7 minimum schema; any §7
  consumer still sees every field it expects.
- The classifier's `memory_type` output has a natural home on the droplet.
