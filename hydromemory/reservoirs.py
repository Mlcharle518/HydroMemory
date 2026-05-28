"""Reservoir enum, name normalization, and non-access behavioral metadata.

The PRD §10 reservoir *access policy* (who may access, obligations) lives in
``hydromemory.governance``. This module owns only the canonical ``Reservoir``
enum, alias normalization for the spec's display names, and the behavioral
metadata (PRD §5.3) the recall scorer reads (e.g. speed -> phase_accessibility).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Reservoir(str, Enum):
    WORKING_STREAM = "working_stream"
    SURFACE = "surface"
    GROUNDWATER = "groundwater"
    GLACIER = "glacier"
    CLOUD = "cloud"
    OCEAN = "ocean"
    CONTAMINATED = "contaminated"
    SACRED = "sacred"


# The spec uses several display names (§5.3/§6/§10/§17); normalize to canonical.
RESERVOIR_ALIASES: dict[str, str] = {
    "surface_reservoir": "surface",
    "cloud_layer": "cloud",
    "contaminated_pool": "contaminated",
    "sacred_spring": "sacred",
    "stream": "working_stream",
    "working": "working_stream",
}


def normalize_reservoir(value: object) -> Reservoir:
    """Map a string (possibly a spec alias) or Reservoir to a canonical Reservoir."""
    if isinstance(value, Reservoir):
        return value
    key = str(value).strip().lower()
    key = RESERVOIR_ALIASES.get(key, key)
    return Reservoir(key)


@dataclass(frozen=True)
class ReservoirBehavior:
    """Non-access behavioral metadata (PRD §5.3).

    ``speed`` in [0,1] feeds recall ``phase_accessibility`` / ``depth_resistance``
    (higher = faster, more readily recalled).
    """

    speed: float
    volatile: bool
    description: str


RESERVOIR_BEHAVIOR: dict[Reservoir, ReservoirBehavior] = {
    Reservoir.WORKING_STREAM: ReservoirBehavior(1.0, True, "Immediate active context; fast, volatile, session-oriented."),
    Reservoir.SURFACE: ReservoirBehavior(0.8, False, "Recently used memories and near-term associations; fast, moderate risk."),
    Reservoir.GROUNDWATER: ReservoirBehavior(0.4, False, "Persistent user patterns and identity-level structures; slow, high impact."),
    Reservoir.GLACIER: ReservoirBehavior(0.2, False, "Frozen high-integrity records and sensitive snapshots; restricted, requires thaw."),
    Reservoir.CLOUD: ReservoirBehavior(0.6, False, "Abstracted pattern clusters; medium speed, useful for distillation."),
    Reservoir.OCEAN: ReservoirBehavior(0.3, False, "Collective or generalized knowledge layer; strong privacy boundaries."),
    Reservoir.CONTAMINATED: ReservoirBehavior(0.0, False, "Unverified, contradictory, or unsafe memory; not usable until filtered."),
    Reservoir.SACRED: ReservoirBehavior(0.5, False, "User-declared values, vows, principles, identity anchors; not casually overwritten."),
}


def behavior_for(reservoir: Reservoir) -> ReservoirBehavior:
    return RESERVOIR_BEHAVIOR[reservoir]
