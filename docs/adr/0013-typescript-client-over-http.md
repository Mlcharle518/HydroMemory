# ADR-0013: TypeScript client over an HTTP/JSON boundary

Status: Accepted

## Context

The PRD positions HydroMemory as a protocol that "AI agents, operating systems,
and platforms can understand and implement", which implies non-Python consumers.
The core engine, however, is Python: the lifecycle, the §5.6 recall scorer, and
the §10 governance logic all live there. A TypeScript consumer could either
reimplement that logic (risking divergence and duplicated, security-relevant
governance code) or call the Python engine across a process boundary.

## Decision

Ship the TypeScript client as a **thin data shim over an HTTP/JSON boundary**, not
as in-process bindings or a reimplementation. `hydromemory/server.py` (FastAPI)
exposes the fully-wired `Engine`; `clients/ts` (`HydroMemoryClient`) only marshals
JSON in and out over `fetch`, typed against a schema/protocol mirror in
`types.ts`. **All scoring, lifecycle, and governance run server-side** — clients
pass at most an `agent` name and `trust` level and cannot smuggle in a decision.
`GET /enums` is the canonical enum contract the client mirrors, and a parity test
pins it.

## Consequences

- There is exactly one implementation of recall and governance (Python); the TS
  client cannot drift from it.
- Cross-language correctness is enforced by the `/enums` parity test rather than
  by manual synchronization.
- The boundary is language-agnostic: any HTTP client can speak the same protocol.
- The cost is a network hop and a running server; for in-process Python callers,
  `Engine` is used directly without the HTTP layer.
