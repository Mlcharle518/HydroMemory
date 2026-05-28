# ADR-0016: In-process bus + a FastAPI WebSocket bridge (no external broker)

Status: Accepted

## Context

PRD §9 describes HydroMemory at the OS level as a publish/subscribe **memory
event bus** that apps and agents use to react to lifecycle changes. ADR-0014
deferred this from v1 and left the seams open (`tick`, `check_access`,
`DropletRepository`). v2 realizes the bus. A "real" event bus could be backed by
an external broker (Redis pub/sub, NATS, Kafka), but that would reintroduce the
external-service dependency ADR-0012 deliberately avoided for storage: it breaks
zero-setup runs, makes the test suite depend on a live broker, and turns
delivery into a nondeterministic, cross-process concern.

## Decision

Implement the bus as an **in-process pub/sub** (`hydromemory.bus.bus.EventBus`)
and demonstrate cross-process delivery through the **FastAPI server**, not a
broker. The bus holds a list of `Subscription`s and fans an event out to matching
subscribers in-process; the only "wire" is the server's `/events` endpoints —
`POST /events` publishes a `MemoryEvent`, and the `/events/subscribe` WebSocket
streams live events to remote clients (`hydromemory.server.create_app`, which
does `bus = EventBus(repo=engine.repo); engine.attach_bus(bus, ...)`). The bus
ships a no-op `NULL_BUS` (`NullEventBus`) as the default so the core stays
event-free until a bus is explicitly wired in.

## Consequences

- Zero-setup is preserved: importing and exercising the bus needs no broker, and
  the in-process suite (`tests/test_v2_bus.py`, the L1–L4 scenario tests) is
  offline and deterministic.
- Cross-process integration is still demonstrated, but only over the FastAPI
  WebSocket bridge — there is **no** message broker and **no** at-least-once /
  durable delivery. Events are best-effort, in-memory, and lost on restart.
- The cross-process surface is WebSocket + an HTTP publish endpoint. Despite the
  "WebSocket/SSE bridge" phrasing in `hydromemory/bus/events.py`'s docstring,
  **no Server-Sent-Events transport is implemented** — `events.py`'s
  JSON-safe `to_dict`/`from_dict` keep events SSE-ready, but only the WebSocket
  path exists in `server.py`. Adding SSE later is an additive endpoint.
- A production deployment can swap the in-process bus for a broker-backed
  implementation of the same `EventBus` surface without touching the verbs,
  pipeline, or the emit seam (ADR-0017).
