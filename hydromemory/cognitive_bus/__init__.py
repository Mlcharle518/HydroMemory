"""Unified HydroCognitive event bus (Master Spec §17).

One bus that routes canonical cognitive objects across **all** stack layers
(memory/intent/judgment/plan/action/reflection/reintegration) by type, owner, and
permission — the cross-layer counterpart to the droplet-centric memory bus
(:mod:`hydromemory.bus`). Routing is by
:class:`~hydromemory.canonical.envelope.ObjectType`; gating is fail-closed on the
canonical §8 envelope (:func:`envelope_allows`) instead of a droplet load, so it
works for every object type. Publishers project layer objects to a
:class:`~hydromemory.canonical.envelope.CanonicalObject` before publishing — the
bus imports only :mod:`hydromemory.canonical`, never a layer schema (ADR-0049).
"""
from __future__ import annotations

from hydromemory.cognitive_bus.bus import (
    NULL_COGNITIVE_BUS,
    CognitiveBus,
    CognitiveHandler,
    CognitiveSubscription,
    NullCognitiveBus,
    envelope_allows,
)
from hydromemory.cognitive_bus.events import CognitiveEvent, utcnow_iso

__all__ = [
    "CognitiveEvent",
    "utcnow_iso",
    "CognitiveBus",
    "NullCognitiveBus",
    "NULL_COGNITIVE_BUS",
    "CognitiveSubscription",
    "CognitiveHandler",
    "envelope_allows",
]
