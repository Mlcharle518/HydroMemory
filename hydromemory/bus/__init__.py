"""HydroMemory memory event bus (PRD §9, v2).

Phase A0 ships the contract: the event model, the bus interface + no-op default,
and the emit seam. Phase B1 adds the concrete sync fan-out (`bus.py`) and the
`BusAgentRuntime` (`runtime.py`).
"""
from hydromemory.bus.bus import NULL_BUS, EventBus, NullEventBus, Subscription
from hydromemory.bus.emit import NULL_EMITTER, Emitter
from hydromemory.bus.events import EventType, MemoryEvent, utcnow_iso
from hydromemory.bus.runtime import (
    STAGE_TOPICS,
    BusAgentRuntime,
    bus_runtime_from_engine,
    topics_for_stages,
)

__all__ = [
    "MemoryEvent",
    "EventType",
    "utcnow_iso",
    "EventBus",
    "NullEventBus",
    "NULL_BUS",
    "Subscription",
    "Emitter",
    "NULL_EMITTER",
    "BusAgentRuntime",
    "bus_runtime_from_engine",
    "topics_for_stages",
    "STAGE_TOPICS",
]
