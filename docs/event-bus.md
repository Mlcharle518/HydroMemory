# The Memory Event Bus (§9)

The memory event bus is the v2 §9 layer that lets the otherwise-synchronous
HydroMemory lifecycle *announce* what it does. Every verb and the capture/recall
pipeline emit a [`MemoryEvent`](#memoryevent) after a mutation; subscribers
(WebSocket clients, the L3 mesh, the bus-driven agent runtime) react to those
events. The bus is **sync at its core**, **permission-gated**, and
**cascade-guarded**.

It lives in [`hydromemory/bus/`](../hydromemory/bus): `events.py` (the event
model), `bus.py` (the `EventBus`), `emit.py` (the emit seam), and `runtime.py`
(the `BusAgentRuntime`). The integration levels that build on it are described in
[integration-levels.md](integration-levels.md); the broader system is in
[architecture.md](architecture.md).

> The bus is **additive**. The default everywhere is a no-op
> ([`NULL_EMITTER`](#the-emit-seam) publishing to `NULL_BUS`), so the v1 engine,
> verbs, pipeline, and the full test suite behave byte-identically until a real
> bus is wired in.

---

## MemoryEvent

A `MemoryEvent` (`hydromemory/bus/events.py`) is the unit published on the bus.
It is a JSON-safe dataclass (`to_dict` / `from_dict`) so it crosses the
WebSocket bridge cleanly.

| Field        | Type               | Meaning                                                   |
| ------------ | ------------------ | --------------------------------------------------------- |
| `type`       | `str`              | An [`EventType`](#the-16-eventtype-topics) value (the topic). |
| `actor`      | `str`              | Who emitted it — an agent name, an `app_id`, or `"system"`. Defaults to `"system"`. |
| `droplet_id` | `str \| None`      | The droplet the event is about (drives the permission gate). |
| `app_id`     | `str \| None`      | The originating app scope, if any.                        |
| `timestamp`  | `str`              | ISO-8601 UTC, defaulted at construction.                  |
| `payload`    | `dict[str, Any]`   | Topic-specific extra data (e.g. `from_phase`/`to_phase`, `score`, `reason`). |

### The 16 EventType topics

`EventType` is a `(str, Enum)`. The string value is the canonical topic name (and
the wire value mirrored by the TS client and `GET /enums`). There are **16**
topics — one per lifecycle/verb effect plus a generic `transformed`:

| Topic         | Emitted by (verb / pipeline)                                  |
| ------------- | ------------------------------------------------------------- |
| `absorbed`    | `Verbs.absorb`, `pipeline.process_experience` (on store)      |
| `flowed`      | `Verbs.flow`                                                  |
| `evaporated`  | `Verbs.evaporate`                                            |
| `condensed`   | `Verbs.condense`                                             |
| `recalled`    | `Verbs.precipitate`, `pipeline.recall_for_agent` (one per result) |
| `infiltrated` | `Verbs.infiltrate`                                          |
| `frozen`      | `Verbs.freeze`                                              |
| `melted`      | `Verbs.melt`                                                |
| `filtered`    | `Verbs.filter`                                             |
| `polluted`    | `Verbs.pollute`                                            |
| `distilled`   | `Verbs.distill`                                            |
| `irrigated`   | `Verbs.irrigate`                                           |
| `drained`     | `Verbs.drain`                                              |
| `archived`    | `Verbs.archive`                                            |
| `forgotten`   | `Verbs.forget` (on actual delete)                          |
| `transformed` | generic phase change — emitted by `evaporate`, and by `infiltrate` when the phase actually changes |

`transformed` is the only non-1:1-with-a-verb topic: `evaporate` emits both
`evaporated` and `transformed`; `infiltrate` emits `infiltrated` and (only if the
phase changed) `transformed`. (`melt` emits only `melted`, not `transformed`.)

---

## The EventBus API

`EventBus` (`hydromemory/bus/bus.py`) is constructed with optional injected
dependencies:

```python
EventBus(*, repo=None, check_access=None, max_depth=1)
```

- `repo` — used to load a droplet for the [permission gate](#permission-gated-delivery). When `None`, delivery is topic-only.
- `check_access` — the governance entry point (defaults to lazily resolving `hydromemory.governance.check_access`).
- `max_depth` — the [cascade guard](#sync-core--async-queue-design) depth (default `1`).

### publish

```python
def publish(self, event: MemoryEvent) -> int
```

Delivers `event` to every matching, permitted subscriber and returns the number
of subscribers actually delivered to. `publish` is a plain synchronous `def`.
There is also `async def apublish(event)` for convenience — it simply calls
`publish` (which never blocks).

Delivery for one subscriber happens only when **all** of:

1. **Topic matches** — the subscription's `topics` is `None` (all topics) or contains `event.type`.
2. **Predicate passes** — the subscription has no `predicate`, or `predicate(event)` returns truthy. A predicate that *raises* is treated as "no match" (isolated).
3. **Permission allows** — see [permission-gated delivery](#permission-gated-delivery).

### subscribe / unsubscribe

```python
def subscribe(self, *, topics=None, predicate=None, handler, subscriber=None) -> Subscription
def unsubscribe(self, sub: Subscription) -> None
```

- `topics`: a `frozenset[str]` of topic values, or `None` for all topics.
- `predicate`: an optional `Callable[[MemoryEvent], bool]` for fine-grained filtering.
- `handler`: either a **sync callable** (invoked inline) or an **`asyncio.Queue`** (duck-typed by `put_nowait` — the WebSocket path).
- `subscriber`: the identity used for the permission gate (an `AgentIdentity`, an object with `.identity()`, a bare `app_id` string, or `None`).

`subscribe` returns a `Subscription` (`id`, `topics`, `predicate`, `handler`,
`subscriber`, `active`). `unsubscribe` marks it inactive and removes it. `publish`
iterates a **snapshot** of the active subscriptions, so a handler may
(un)subscribe during dispatch without corrupting the loop.

### Subscriber identity coercion

The `subscriber` is resolved to a governance identity for the gate:

- `None` -> topic-only delivery (the gate is skipped).
- an object with a `trust_level` attribute -> used as-is.
- an object with an `.identity()` method (e.g. a `BaseAgent`) -> that identity (resolution errors are swallowed -> topic-only).
- anything else (e.g. a bare `app_id` string) -> coerced to `AgentIdentity(name=str(subscriber), trust_level=SESSION)`.

---

## Sync-core + async-queue design

The bus core is synchronous **by design**:

- **The v1 path stays loop-free.** Verbs and the pipeline are synchronous; they emit inline with no event loop. A handler that is a plain callable runs inline during `publish`.
- **WebSocket subscribers drain a bounded queue.** A handler that is an `asyncio.Queue` is fed with `put_nowait`. On a **full** queue the oldest item is dropped (`get_nowait` then `put_nowait`) so `publish` *never blocks* on a slow client. This is the seam the server's WebSocket bridge drains on the event loop.

### Error isolation

A handler, a predicate, or the droplet load raising an exception never stops the
fan-out to the remaining subscribers. A failing **handler** counts as
not-delivered; a failing **predicate** counts as no-match; a failing **droplet
load** falls back to topic-only delivery; a failing **permission check** denies
that subscriber (fail-closed) but is isolated.

### Cascade (re-entrancy) guard

`publish` tracks dispatch depth. A nested `publish` is dropped once depth would
exceed `max_depth`; the check is `if self._depth > self._max_depth: return 0`.
With the default `max_depth=1`, the top-level publish (depth 0) and exactly one
level of nested publish (depth 1) are delivered; a publish nested two levels deep
(depth 2) is dropped. Empirically, a handler that re-publishes on every event
produces deliveries at depths `[0, 1]` and then stops — preventing event storms.

> This bus-level depth guard is **separate** from the L3 mesh's own cascade
> guard, which counts a `_depth` integer carried in the event *payload*. The two
> mechanisms are independent; see [integration-levels.md](integration-levels.md#cascade-safety).

---

## Permission-gated delivery

When an event names a `droplet_id` **and** the bus has a `repo`, the bus loads
the droplet once (shared across subscribers) and decides delivery **per
subscriber** via:

```python
check_access(droplet, identity, AccessContext(), Operation.READ)
```

A subscriber never receives an event about a droplet it cannot READ.

Delivery falls back to **topic-only** (no gate) when any of these hold: the event
has no `droplet_id`; the bus has no `repo`; the droplet could not be loaded; or
the subscriber has no identity (`subscriber=None`).

Two consequences worth calling out, both following directly from the gate using a
default `AccessContext()`:

- **Context-free READ.** The gate always uses a fresh `AccessContext()`
  (`consent_granted=False`, `thaw_granted=False`, `safe_context=False`,
  `recall_mode=None`). The bus cannot supply consent/thaw, so any obligation that
  *hard-denies* without granted context will deny at the bus.
- **Glacier denies all subscribers.** The glacier reservoir requires the thaw
  protocol (`requires_thaw_protocol`), which hard-denies a READ when
  `thaw_granted` is false. Because the bus's `AccessContext()` never grants thaw,
  an event about a glacier/ICE droplet is delivered to **no** identified
  subscriber. (An anonymous, identity-less subscriber still gets topic-only
  delivery, since the gate is skipped entirely for `subscriber=None`.)

---

## The emit seam

`Emitter` (`hydromemory/bus/emit.py`) is the one-line helper the engine and verbs
use to publish under a fixed `actor`/`app_id`:

```python
class Emitter:
    def __init__(self, bus, *, actor="engine", app_id=None): ...
    def emit(self, event_type, *, droplet_id=None, payload=None) -> MemoryEvent
```

`emit` builds the `MemoryEvent`, publishes it, and returns it.

- **`NULL_EMITTER`** is the module-level default — an `Emitter` bound to the
  shared no-op `NULL_BUS`. `Verbs.emit` and the pipeline `emit=` parameters
  default to it, so emission is a no-op unless a real bus is attached.
- **Wiring a real bus.** `build_engine(config, bus=...)` constructs the engine
  with an `Emitter(bus)`; `Engine.attach_bus(bus, *, actor="engine", app_id=None)`
  swaps in a live emitter on an existing engine (it sets both `engine.emit` and
  `engine.verbs.emit`).

### Emission points

Each verb emits **after** its mutation and repo write (so a subscriber that loads
the droplet sees the post-mutation state). The capture pipeline
(`process_experience`) emits `absorbed` only when the governance review allows the
store; the recall pipeline (`recall_for_agent`) and `Verbs.precipitate` emit one
`recalled` per ranked result. See the [topic table](#the-16-eventtype-topics) for
the full per-verb mapping.

---

## BusAgentRuntime

`BusAgentRuntime` (`hydromemory/bus/runtime.py`) is the §9 counterpart to the
synchronous `AgentRuntime`. Instead of an ordered in-process `tick(stage)` loop,
each §8 agent is **subscribed** to the bus on the topics that correspond to the
lifecycle stages it handles. This module is purely additive — it does **not**
modify `AgentRuntime.tick`; the synchronous seam stays intact.

- **Stage -> topic mapping.** `STAGE_TOPICS` maps each stage an agent `handles`
  (`capture`, `maintain`, `recall`, `expose`, `filter`, `reflect`, `distill`,
  `archive`) to the bus topics it reacts to. `topics_for_stages(stages)` turns an
  agent's declared stages into a topic set: empty stages -> `None` (all topics);
  an agent declaring *only* unknown stages gets an empty set and never fires
  (intentional, rather than silently receiving everything).
- **Delivery -> invocation.** On a delivered event the runtime builds an
  `AgentContext` (a derived `stage` label, `payload={"event": event}`), calls
  `agent.run(ctx)` guarded against exceptions, and records the latest result per
  agent on `last_results` plus a per-agent `handled` count.
- **Construction.** `bus_runtime_from_engine(engine, bus)` mirrors the v1
  `build_default_runtime`: it registers all eight §8 roles in the same order,
  sharing the injected engine, but wired as bus subscribers.

---

## HTTP surface

The reference server (`hydromemory/server.py`) wires a real `EventBus` over the
engine's repo during startup (`engine.attach_bus(bus, actor="server")`), so the
engine's verbs + pipeline emit onto it.

### POST /events

Publish a `MemoryEvent` onto the bus; returns the delivery count.

```http
POST /events
{
  "type": "absorbed",
  "droplet_id": "mem_123",
  "app_id": "calendar",
  "payload": {"note": "demo"},
  "agent": "calendar_app",
  "trust": "approved"
}
-> {"delivered": 2}
```

The `actor` is recomputed **server-side** from `agent`/`trust` — a client cannot
forge a privileged actor any more than it can forge a governance decision.

### WS /events/subscribe

Stream live bus events to a WebSocket client.

- Query params: `agent` / `trust` (the subscriber identity for the permission
  gate) and an optional comma-separated `topics` filter.
- The server registers a bounded `asyncio.Queue(maxsize=100)` as the bus handler
  (drop-oldest when full), then runs two concurrent tasks: a *pump* that awaits
  `queue.get()` and forwards each event as a JSON frame, and a *receiver* that
  awaits `websocket.receive()` purely to notice a client disconnect. When either
  settles, the other is cancelled and the subscription is torn down.

### TypeScript client

`HydroMemoryClient` (`clients/ts/src/client.ts`) mirrors both:

- `publishEvent(evt)` — `POST /events`; resolves to the `delivered` count.
- `subscribeEvents(options, onEvent)` — opens `WS /events/subscribe` (rewriting the base `http(s)` URL to `ws(s)`), parses each frame into a `MemoryEvent`, and returns a disposer that closes the socket.

The TS `EventType` union in `clients/ts/src/types.ts` mirrors the 16 Python topic
values exactly and is pinned by the cross-language parity test.

---

## Usage example

```python
from hydromemory.bus import EventBus, EventType
from hydromemory.engine import build_engine
from hydromemory.config import HydroConfig

# 1. Build an engine wired to a real bus (permission-gated on the engine's repo).
engine = build_engine(HydroConfig())
bus = EventBus(repo=engine.repo)
engine.attach_bus(bus, actor="demo")

# 2. Subscribe to a couple of topics with a sync handler.
seen = []
sub = bus.subscribe(
    topics=frozenset({EventType.ABSORBED.value, EventType.RECALLED.value}),
    handler=lambda e: seen.append((e.type, e.droplet_id)),
)

# 3. Drive the lifecycle — verbs/pipeline emit automatically.
engine.absorb("I prefer concise answers.", source="chat")
engine.recall("how do I like answers")

# `seen` now holds ("absorbed", <id>) and ("recalled", <id>) tuples
# (subject to the permission gate on each droplet).
bus.unsubscribe(sub)
```
