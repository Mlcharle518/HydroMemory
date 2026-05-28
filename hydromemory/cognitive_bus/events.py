"""Cognitive event model for the unified HydroCognitive event bus (Master Spec §17).

A :class:`CognitiveEvent` is the unit published on the cross-layer bus when *any*
layer moves a cognitive object — a memory absorbed, an intent formed, a judgment
made, a plan drawn, an action taken, a reflection learned, a reintegration
committed. Unlike :class:`~hydromemory.bus.events.MemoryEvent` (droplet-centric,
topic = lifecycle verb), a cognitive event carries the **canonical §8 envelope**
(:class:`~hydromemory.canonical.envelope.CanonicalObject`) so the bus can route
and gate uniformly across all object types without loading a layer body.

Events are JSON-safe (``to_dict``/``from_dict``) so they cross the audit trail and
any future WebSocket/SSE bridge, mirroring the memory event contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from hydromemory.canonical.envelope import CanonicalObject, ObjectType


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CognitiveEvent:
    """A cross-layer cognitive event: an envelope + the verb that produced it.

    ``verb`` is a :class:`~hydromemory.canonical.verbs.CanonicalVerb` value (e.g.
    ``"ABSORB"``, ``"JUDGE"``) or a free string. ``actor`` is the agent/identity/
    "system" string that emitted it. The :attr:`object_type` / :attr:`object_id`
    accessors delegate to the carried envelope so subscribers route without
    reaching into ``object_ref`` directly.
    """

    object_ref: CanonicalObject
    verb: str
    actor: str = "system"
    timestamp: str = field(default_factory=utcnow_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def object_type(self) -> ObjectType:
        return self.object_ref.object_type

    @property
    def object_id(self) -> str:
        return self.object_ref.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_ref": self.object_ref.to_dict(),
            "verb": self.verb,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CognitiveEvent:
        data = dict(data)
        return cls(
            object_ref=CanonicalObject.from_dict(data["object_ref"]),
            verb=str(data["verb"]),
            actor=str(data.get("actor", "system")),
            timestamp=str(data.get("timestamp") or utcnow_iso()),
            payload=dict(data.get("payload") or {}),
        )


__all__ = ["CognitiveEvent", "utcnow_iso"]
