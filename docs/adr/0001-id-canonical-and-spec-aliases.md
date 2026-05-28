# ADR-0001: `id` is canonical; spec aliases accepted on ingest

Status: Accepted

## Context

The PRD is internally inconsistent about identity and a few field names. The §7
minimum schema names the identifier `id`, but the §5.2 and §10.1 example droplets
use `memory_id`. Similarly, §5.2 expresses ownership/scope with `scope` (e.g.
`"user_private"`) and `agent_access`, while §7 uses `owner` + `visibility` +
`allowed_agents`. The §12 Example A droplet uses `charge` where the state vector
field is `emotional_charge`. If the data model rejected these, the spec's own
example blobs would not load.

## Decision

The canonical field is `id` (produced by `new_id()` as `mem_<8 hex>`). The
alias-tolerant `Droplet.from_dict` (and `Permissions.from_dict` / `State.from_dict`)
accept the spec's aliases on ingest and normalize them:

- `memory_id` -> `id`
- `charge` -> `emotional_charge`
- `scope` -> `owner` + `visibility` (e.g. `user_private` -> owner `user`,
  visibility `private`)
- `agent_access` -> `allowed_agents`

Unknown top-level keys (e.g. §10.1's `reason`, `usable_for_generation`) are
preserved into `meta` rather than dropped.

## Consequences

- Every example blob in the PRD round-trips losslessly through `from_dict`.
- The rest of the codebase only ever sees the canonical names, so serialization
  (`to_dict`), querying, and the HTTP/TS contract stay simple and singular.
- The alias map is a small, documented surface in `schema.py`; new aliases are
  cheap to add but must be added deliberately.
