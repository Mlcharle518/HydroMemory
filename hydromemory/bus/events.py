"""Memory event model for the §9 memory event bus (v2).

A :class:`MemoryEvent` is the unit published on the bus when the lifecycle moves
a droplet (absorbed, transformed, recalled, frozen, forgotten, …). Events are
JSON-safe (``to_dict``/``from_dict``) so they cross the WebSocket/SSE bridge and
can be recorded in the audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Canonical bus topics — one per lifecycle/verb effect, plus TRANSFORMED."""

    ABSORBED = "absorbed"
    FLOWED = "flowed"
    EVAPORATED = "evaporated"
    CONDENSED = "condensed"
    RECALLED = "recalled"
    INFILTRATED = "infiltrated"
    FROZEN = "frozen"
    MELTED = "melted"
    FILTERED = "filtered"
    POLLUTED = "polluted"
    DISTILLED = "distilled"
    IRRIGATED = "irrigated"
    DRAINED = "drained"
    ARCHIVED = "archived"
    FORGOTTEN = "forgotten"
    TRANSFORMED = "transformed"  # generic phase change
    # HydroIntent layer (additive; ADR-0037/0042). The subject id rides in droplet_id.
    INTENT_DETECTED = "intent_detected"
    INTENT_PROPOSED = "intent_proposed"
    INTENT_ACTIVATED = "intent_activated"
    INTENT_DEFERRED = "intent_deferred"
    INTENT_SUPPRESSED = "intent_suppressed"
    INTENT_CONFLICT_DETECTED = "intent_conflict_detected"
    INTENT_RESOLVED = "intent_resolved"
    INTENT_MERGED = "intent_merged"
    INTENT_SPLIT = "intent_split"
    INTENT_HANDED_OFF = "intent_handed_off"
    INTENT_RETIRED = "intent_retired"
    INTENT_DELETED = "intent_deleted"


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class MemoryEvent:
    type: str  # an EventType value (the topic)
    actor: str = "system"  # AgentIdentity.name / app_id / "system"
    droplet_id: str | None = None
    app_id: str | None = None
    timestamp: str = field(default_factory=utcnow_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "actor": self.actor,
            "droplet_id": self.droplet_id,
            "app_id": self.app_id,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEvent:
        data = dict(data)
        return cls(
            type=str(data["type"]),
            actor=str(data.get("actor", "system")),
            droplet_id=data.get("droplet_id"),
            app_id=data.get("app_id"),
            timestamp=str(data.get("timestamp") or utcnow_iso()),
            payload=dict(data.get("payload") or {}),
        )
