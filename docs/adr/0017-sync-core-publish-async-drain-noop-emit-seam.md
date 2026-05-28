# ADR-0017: Sync-core `publish` + async-drained subscribers; a no-op emit seam

Status: Accepted

## Context

The v1 verbs, the capture/recall pipeline, and all 276 v1 tests are synchronous
and run without an event loop. The §9 bus must let them emit lifecycle events,
yet a bus whose `publish` was `async` would force an event loop (or an
`asyncio.run` per call) into the synchronous core — infecting the verbs, the
pipeline, and every test with `async`/`await`, and breaking the byte-identical v1
behavior ADR-0014 promised. At the same time, a slow WebSocket subscriber must
never be able to block (or back-pressure) a `publish` happening on the synchronous
hot path.

## Decision

Make the bus **sync at its core** and add a **no-op emit seam** so emission is
opt-in:

- `EventBus.publish` is a plain `def` (`hydromemory.bus.bus`). It iterates a
  *snapshot* of active subscriptions and invokes sync-callable handlers inline;
  it returns the delivered count. An `apublish` coroutine exists only as a thin
  convenience that calls the sync `publish` (which never blocks).
- WebSocket subscribers are fed via a **bounded `asyncio.Queue`** rather than a
  callback: the bus duck-types a queue by its `put_nowait` and, on a full queue,
  **drops the oldest** item (`_enqueue` does `get_nowait()` then `put_nowait()`)
  so `publish` never blocks on a slow client. The server's `/events/subscribe`
  coroutine drains that queue and forwards each event (`hydromemory.server`).
- The verbs and pipeline gained an `emit` parameter defaulting to `NULL_EMITTER`
  (`hydromemory.bus.emit`), which publishes to `NULL_BUS` (a bus that drops
  everything). `Verbs.emit` defaults to `NULL_EMITTER` (`hydromemory/verbs.py`),
  `process_experience` / `recall_for_agent` default their `emit=NULL_EMITTER`
  (`hydromemory/pipeline.py`), and `Engine.emit` defaults to `NULL_EMITTER` with
  `Engine.attach_bus` swapping in a live `Emitter` (`hydromemory/engine.py`).

## Consequences

- The synchronous verbs/pipeline/tests emit without an event loop; with the
  default `NULL_EMITTER` they behave byte-identically to v1 (the suite stayed at
  276 v1 tests green; see ADR-0025).
- A slow or stalled WebSocket client cannot stall the producer: its queue is
  bounded and drop-oldest, so live event streams are best-effort and may shed the
  oldest events under load (newest-wins). This is a deliberate trade of
  completeness for never blocking the hot path.
- Emission is strictly opt-in and centralized at one seam; nothing emits until
  `Engine.attach_bus` (or an explicit `Emitter`) is wired in, so the blast radius
  of the bus on existing code is exactly the `emit` default.
