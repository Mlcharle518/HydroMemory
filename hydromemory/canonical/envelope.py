"""Canonical cross-layer object envelope (Master Spec §8: Minimum Shared Metadata).

Every HydroCognitive object — observation, memory droplet, identity anchor, intent, judgment,
plan, action, reflection, reintegration — can be projected to one shared envelope so that the
unified HydroCognitive event bus (§17) and HydroIntegrate (the loop-closer) can route, gate, and
audit objects uniformly.

This module defines the canonical shapes ONLY and imports nothing from the layer packages, so it
stays a stable interop contract. The per-layer mappings live in
:mod:`hydromemory.canonical.projection`. Existing layer dataclasses are never mutated — projection
is additive (ADR-0025, ADR-0047).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# §8 visibility vocabulary (kept as a plain string on the envelope to stay serialization-faithful
# and dependency-light; the memory layer's richer Visibility enum projects onto these values).
VISIBILITIES = frozenset({"private", "shared", "public"})


class ObjectType(str, Enum):
    """The nine canonical object types (Master Spec §7), one per stack layer."""

    OBSERVATION = "observation"
    MEMORY = "memory"
    IDENTITY = "identity"
    INTENT = "intent"
    JUDGMENT = "judgment"
    PLAN = "plan"
    ACTION = "action"
    REFLECTION = "reflection"
    REINTEGRATION = "reintegration"


def _clamp01(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


@dataclass
class CanonicalPermissions:
    """§8 permissions block — the cross-layer subset (a projection of richer per-layer types)."""

    visibility: str = "private"
    allowed_agents: list[str] = field(default_factory=list)
    external_sharing: bool = False
    requires_user_review: bool = False

    def __post_init__(self) -> None:
        if self.visibility not in VISIBILITIES:
            self.visibility = "private"

    def to_dict(self) -> dict[str, Any]:
        return {
            "visibility": self.visibility,
            "allowed_agents": list(self.allowed_agents),
            "external_sharing": bool(self.external_sharing),
            "requires_user_review": bool(self.requires_user_review),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CanonicalPermissions:
        data = dict(data or {})
        return cls(
            visibility=str(data.get("visibility", "private")),
            allowed_agents=list(data.get("allowed_agents", []) or []),
            external_sharing=bool(data.get("external_sharing", False)),
            requires_user_review=bool(data.get("requires_user_review", False)),
        )


@dataclass
class CanonicalLinks:
    """§8 links block — typed relations between canonical objects (cross-layer)."""

    derived_from: list[str] = field(default_factory=list)
    supports: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "derived_from": list(self.derived_from),
            "supports": list(self.supports),
            "contradicts": list(self.contradicts),
            "supersedes": list(self.supersedes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CanonicalLinks:
        data = dict(data or {})
        return cls(
            derived_from=list(data.get("derived_from", []) or []),
            supports=list(data.get("supports", []) or []),
            contradicts=list(data.get("contradicts", []) or []),
            supersedes=list(data.get("supersedes", []) or []),
        )


@dataclass
class CanonicalAudit:
    """§8 audit block — provenance + reversibility reference."""

    created_by: str = "system"
    last_updated: datetime | None = None
    rollback_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_by": self.created_by,
            "last_updated": _iso(self.last_updated),
            "rollback_ref": self.rollback_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CanonicalAudit:
        data = dict(data or {})
        return cls(
            created_by=str(data.get("created_by", "system")),
            last_updated=_parse_dt(data.get("last_updated")),
            rollback_ref=(str(data["rollback_ref"]) if data.get("rollback_ref") else None),
        )


@dataclass
class CanonicalObject:
    """The §8 minimum-shared-metadata envelope.

    A layer object is projected to this shape (see :mod:`hydromemory.canonical.projection`); the
    envelope carries only routing/gating/audit metadata, never the layer-specific body. ``confidence``
    and ``sensitivity`` are clamped to [0, 1].
    """

    id: str
    object_type: ObjectType
    source: str = ""
    created_at: datetime | None = None
    owner: str = "user"
    confidence: float = 0.0
    sensitivity: float = 0.0
    permissions: CanonicalPermissions = field(default_factory=CanonicalPermissions)
    links: CanonicalLinks = field(default_factory=CanonicalLinks)
    audit: CanonicalAudit = field(default_factory=CanonicalAudit)

    def __post_init__(self) -> None:
        if not isinstance(self.object_type, ObjectType):
            self.object_type = ObjectType(str(self.object_type))
        self.confidence = _clamp01(self.confidence)
        self.sensitivity = _clamp01(self.sensitivity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object_type": self.object_type.value,
            "source": self.source,
            "created_at": _iso(self.created_at),
            "owner": self.owner,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "permissions": self.permissions.to_dict(),
            "links": self.links.to_dict(),
            "audit": self.audit.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalObject:
        data = dict(data)
        return cls(
            id=str(data["id"]),
            object_type=ObjectType(str(data["object_type"])),
            source=str(data.get("source", "")),
            created_at=_parse_dt(data.get("created_at")),
            owner=str(data.get("owner", "user")),
            confidence=_clamp01(data.get("confidence", 0.0)),
            sensitivity=_clamp01(data.get("sensitivity", 0.0)),
            permissions=CanonicalPermissions.from_dict(data.get("permissions")),
            links=CanonicalLinks.from_dict(data.get("links")),
            audit=CanonicalAudit.from_dict(data.get("audit")),
        )


__all__ = [
    "VISIBILITIES",
    "ObjectType",
    "CanonicalPermissions",
    "CanonicalLinks",
    "CanonicalAudit",
    "CanonicalObject",
]
