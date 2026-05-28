"""The emit seam: a one-line helper for the engine/verbs to publish events.

``Emitter`` binds a bus + a default actor/app_id so a verb emits in one call.
:data:`NULL_EMITTER` is the default everywhere in v1 — it publishes to
:data:`NULL_BUS` (a no-op), so the existing engine, verbs, pipeline, and all 276
v1 tests behave byte-identically until a real bus is wired in (v2 Phase B1).
"""
from __future__ import annotations

from typing import Any

from hydromemory.bus.bus import NULL_BUS, EventBus
from hydromemory.bus.events import EventType, MemoryEvent


class Emitter:
    """Publishes :class:`MemoryEvent`s to a bus under a fixed actor/app_id."""

    def __init__(self, bus: EventBus, *, actor: str = "engine", app_id: str | None = None) -> None:
        self.bus = bus
        self.actor = actor
        self.app_id = app_id

    def emit(
        self,
        event_type: EventType | str,
        *,
        droplet_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> MemoryEvent:
        event = MemoryEvent(
            type=str(getattr(event_type, "value", event_type)),
            actor=self.actor,
            droplet_id=droplet_id,
            app_id=self.app_id,
            payload=dict(payload or {}),
        )
        self.bus.publish(event)
        return event


#: The default no-op emitter (publishes to NULL_BUS). v1 uses this everywhere.
NULL_EMITTER: Emitter = Emitter(NULL_BUS, actor="engine")
