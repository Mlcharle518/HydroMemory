"""HydroMemory droplet schema, state vector, enums, and (de)serialization.

The :class:`Droplet` is the atomic unit of HydroMemory (PRD §5.2, §7). This
module owns the canonical data model and an alias-tolerant ``from_dict`` so the
spec's own example blobs (§5.2, §12, §10.1) round-trip without data loss.

Reconciliations (see docs/adr): the canonical id field is ``id`` (``memory_id``
accepted on ingest); ``type``/``semantic_tags`` are first-class though §7 omits
them; the state vector is the §7 nine floats plus an optional ``emotional_charge``
(``charge`` accepted as an alias); ``Phase`` carries all 13 §5.4 values while
``STORABLE_PHASES`` is the §7 persisted subset.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from hydromemory.reservoirs import Reservoir, normalize_reservoir


class Phase(str, Enum):
    LIQUID = "liquid"
    VAPOR = "vapor"
    CLOUD = "cloud"
    RAIN = "rain"
    RIVER = "river"
    GROUNDWATER = "groundwater"
    ICE = "ice"
    SNOW = "snow"
    FOG = "fog"
    STEAM = "steam"
    OCEAN = "ocean"
    POLLUTED = "polluted"
    FILTERED = "filtered"


# Persisted / queryable phases (PRD §7). The four transient phases
# (river/snow/fog/steam) are derived recall/lifecycle states, never stored.
STORABLE_PHASES: frozenset[Phase] = frozenset(
    {
        Phase.LIQUID,
        Phase.VAPOR,
        Phase.CLOUD,
        Phase.RAIN,
        Phase.GROUNDWATER,
        Phase.ICE,
        Phase.OCEAN,
        Phase.POLLUTED,
        Phase.FILTERED,
    }
)
TRANSIENT_PHASES: frozenset[Phase] = frozenset(p for p in Phase) - STORABLE_PHASES


class Visibility(str, Enum):
    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"


class Retention(str, Enum):
    TEMPORARY = "temporary"
    PERSISTENT = "persistent"
    ARCHIVED = "archived"


# The nine canonical §7 state floats, plus emotional_charge (§5.2). All in [0,1].
CANONICAL_STATE_FIELDS: tuple[str, ...] = (
    "temperature",
    "pressure",
    "gravity",
    "purity",
    "salinity",
    "depth",
    "fluidity",
    "integrity",
    "confidence",
)
STATE_FIELDS: tuple[str, ...] = CANONICAL_STATE_FIELDS + ("emotional_charge",)

# Aliases accepted on ingest for state floats (PRD §12 Example A uses "charge").
_STATE_ALIASES: dict[str, str] = {"charge": "emotional_charge"}


def clamp_unit(x: float) -> float:
    """Clamp a value into the [0, 1] range used for all state floats."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


@dataclass
class State:
    temperature: float = 0.0
    pressure: float = 0.0
    gravity: float = 0.0
    purity: float = 0.0
    salinity: float = 0.0
    depth: float = 0.0
    fluidity: float = 0.0
    integrity: float = 0.0
    confidence: float = 0.0
    emotional_charge: float = 0.0

    def clamped(self) -> State:
        return State(
            temperature=clamp_unit(self.temperature),
            pressure=clamp_unit(self.pressure),
            gravity=clamp_unit(self.gravity),
            purity=clamp_unit(self.purity),
            salinity=clamp_unit(self.salinity),
            depth=clamp_unit(self.depth),
            fluidity=clamp_unit(self.fluidity),
            integrity=clamp_unit(self.integrity),
            confidence=clamp_unit(self.confidence),
            emotional_charge=clamp_unit(self.emotional_charge),
        )

    def to_dict(self) -> dict[str, float]:
        return {f: float(getattr(self, f)) for f in STATE_FIELDS}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> State:
        data = dict(data or {})
        for alias, canon in _STATE_ALIASES.items():
            if alias in data and canon not in data:
                data[canon] = data.pop(alias)

        def g(key: str) -> float:
            v = data.get(key)
            return float(v) if v is not None else 0.0

        return cls(
            temperature=g("temperature"),
            pressure=g("pressure"),
            gravity=g("gravity"),
            purity=g("purity"),
            salinity=g("salinity"),
            depth=g("depth"),
            fluidity=g("fluidity"),
            integrity=g("integrity"),
            confidence=g("confidence"),
            emotional_charge=g("emotional_charge"),
        )


@dataclass
class Permissions:
    owner: str = "user"
    visibility: Visibility = Visibility.PRIVATE
    allowed_agents: list[str] = field(default_factory=list)
    retention: Retention = Retention.TEMPORARY
    external_sharing: bool = False
    requires_consent_for_external_use: bool = False
    requires_user_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "visibility": self.visibility.value,
            "allowed_agents": list(self.allowed_agents),
            "retention": self.retention.value,
            "external_sharing": self.external_sharing,
            "requires_consent_for_external_use": self.requires_consent_for_external_use,
            "requires_user_review": self.requires_user_review,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Permissions:
        data = dict(data or {})
        # §5.2 alias: scope -> owner + visibility
        if "scope" in data:
            scope = str(data.pop("scope"))
            if scope == "user_private":
                data.setdefault("owner", "user")
                data.setdefault("visibility", "private")
            elif scope in {v.value for v in Visibility}:
                data.setdefault("visibility", scope)
            else:
                data.setdefault("visibility", "private")
        # §5.2 alias: agent_access -> allowed_agents
        if "agent_access" in data and "allowed_agents" not in data:
            data["allowed_agents"] = data.pop("agent_access")
        return cls(
            owner=str(data.get("owner", "user")),
            visibility=Visibility(data.get("visibility", "private")),
            allowed_agents=list(data.get("allowed_agents", []) or []),
            retention=Retention(data.get("retention", "temporary")),
            external_sharing=bool(data.get("external_sharing", False)),
            requires_consent_for_external_use=bool(data.get("requires_consent_for_external_use", False)),
            requires_user_review=bool(data.get("requires_user_review", False)),
        )


@dataclass
class Links:
    associations: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    supports: list[str] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "associations": list(self.associations),
            "contradictions": list(self.contradictions),
            "supports": list(self.supports),
            "derived_from": list(self.derived_from),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Links:
        data = dict(data or {})
        return cls(
            associations=list(data.get("associations", []) or []),
            contradictions=list(data.get("contradictions", []) or []),
            supports=list(data.get("supports", []) or []),
            derived_from=list(data.get("derived_from", []) or []),
        )


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


@dataclass
class Cycle:
    cycle_count: int = 0
    last_recalled: datetime | None = None
    last_transformed: datetime | None = None
    last_verified: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_count": self.cycle_count,
            "last_recalled": _fmt_dt(self.last_recalled),
            "last_transformed": _fmt_dt(self.last_transformed),
            "last_verified": _fmt_dt(self.last_verified),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Cycle:
        data = dict(data or {})
        return cls(
            cycle_count=int(data.get("cycle_count", 0) or 0),
            last_recalled=_parse_dt(data.get("last_recalled")),
            last_transformed=_parse_dt(data.get("last_transformed")),
            last_verified=_parse_dt(data.get("last_verified")),
        )


def new_id() -> str:
    return f"mem_{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Top-level keys recognised on ingest (so unknown keys can be preserved in meta).
_TOP_LEVEL_STATE_KEYS: frozenset[str] = frozenset(set(STATE_FIELDS) | set(_STATE_ALIASES))
_KNOWN_TOP_KEYS: frozenset[str] = (
    frozenset(
        {
            "id",
            "memory_id",
            "content",
            "source",
            "created_at",
            "phase",
            "reservoir",
            "type",
            "memory_type",
            "semantic_tags",
            "tags",
            "context",
            "state",
            "permissions",
            "links",
            "cycle",
            "meta",
            "embedding",
        }
    )
    | _TOP_LEVEL_STATE_KEYS
)


@dataclass
class Droplet:
    id: str
    content: str = ""
    source: str = "unknown"
    created_at: datetime = field(default_factory=_utcnow)
    phase: Phase = Phase.LIQUID
    reservoir: Reservoir = Reservoir.WORKING_STREAM
    memory_type: str | None = None
    semantic_tags: list[str] = field(default_factory=list)
    state: State = field(default_factory=State)
    permissions: Permissions = field(default_factory=Permissions)
    links: Links = field(default_factory=Links)
    cycle: Cycle = field(default_factory=Cycle)
    meta: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None

    def to_dict(self, include_embedding: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "created_at": _fmt_dt(self.created_at),
            "phase": self.phase.value,
            "reservoir": self.reservoir.value,
            "memory_type": self.memory_type,
            "semantic_tags": list(self.semantic_tags),
            "state": self.state.to_dict(),
            "permissions": self.permissions.to_dict(),
            "links": self.links.to_dict(),
            "cycle": self.cycle.to_dict(),
        }
        if self.meta:
            out["meta"] = dict(self.meta)
        if include_embedding and self.embedding is not None:
            out["embedding"] = list(self.embedding)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Droplet:
        data = dict(data)

        droplet_id = data.get("id") or data.get("memory_id") or new_id()
        memory_type = data.get("memory_type", data.get("type"))

        # semantic_tags: explicit, or "tags", or a list-valued "context".
        ctx = data.get("context")
        tags = data.get("semantic_tags")
        if tags is None:
            tags = data.get("tags")
        if tags is None and isinstance(ctx, list):
            tags = ctx
        semantic_tags = list(tags) if tags else []

        # State: nested dict + top-level float fallbacks (Example A) + aliases.
        state_src: dict[str, Any] = dict(data.get("state") or {})
        for key in _TOP_LEVEL_STATE_KEYS:
            if key in data:
                state_src.setdefault(key, data[key])
        state = State.from_dict(state_src)

        # Preserve any unknown top-level keys (e.g. §10.1 reason/usable_for_generation).
        meta: dict[str, Any] = dict(data.get("meta") or {})
        for key, value in data.items():
            if key not in _KNOWN_TOP_KEYS:
                meta[key] = value
        if isinstance(ctx, dict):
            meta.setdefault("context", ctx)

        return cls(
            id=str(droplet_id),
            content=str(data.get("content", "")),
            source=str(data.get("source", "unknown")),
            created_at=_parse_dt(data.get("created_at")) or _utcnow(),
            phase=Phase(data.get("phase", "liquid")),
            reservoir=normalize_reservoir(data.get("reservoir", "working_stream")),
            memory_type=memory_type,
            semantic_tags=semantic_tags,
            state=state,
            permissions=Permissions.from_dict(data.get("permissions")),
            links=Links.from_dict(data.get("links")),
            cycle=Cycle.from_dict(data.get("cycle")),
            meta=meta,
            embedding=data.get("embedding"),
        )
