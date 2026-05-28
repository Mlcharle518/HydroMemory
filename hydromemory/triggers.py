"""Trigger and event layer (PRD §5.5).

Triggers are the *forces* that drive phase transitions (§5.4). Two families:

- **Natural forces** (the §5.5 table): ``HEAT, PRESSURE, GRAVITY, WIND, TERRAIN,
  SALT, COLD, STORM, FILTRATION, POLLUTION``. Each maps to an external signal
  (attention, urgency, importance, social input, ...).
- **Synthetic triggers** emitted by the engine itself while a droplet matures:
  ``SIMILARITY, ASSOCIATION, REPETITION, DENSITY, EXTREME_CHARGE, SAFE_CONTEXT,
  REINTEGRATION``. These complete the §5.4 transition chain (e.g. ``VAPOR +
  SIMILARITY -> CLOUD``) which references conditions that are not raw forces.

:func:`detect_triggers` reads the droplet's :class:`~hydromemory.schema.State`
floats together with a free-form ``context`` dict and returns the set of fired
triggers. The mapping (and its thresholds, in :class:`TriggerConfig`) is
documented inline below.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hydromemory.schema import Droplet


class Trigger(str, Enum):
    # --- Natural forces (PRD §5.5) ------------------------------------------
    HEAT = "heat"            # attention, novelty, emotional activation
    PRESSURE = "pressure"    # urgency, stakes, unresolved tension
    GRAVITY = "gravity"      # importance, consequence, identity relevance
    WIND = "wind"            # social input, language, communication, outside influence
    TERRAIN = "terrain"      # user personality, context, platform environment
    SALT = "salt"            # emotional residue, bias, symbolic meaning
    COLD = "cold"            # preservation, silence, distance, reflection
    STORM = "storm"          # crisis, conflict, rapid change
    FILTRATION = "filtration"  # verification, correction, therapy, reasoning, evidence
    POLLUTION = "pollution"  # misinformation, contradiction, manipulation, noise

    # --- Synthetic / engine-emitted triggers (complete the §5.4 chain) ------
    SIMILARITY = "similarity"          # vapor droplets resemble each other -> cluster
    ASSOCIATION = "association"        # rained memory links into an associative chain
    REPETITION = "repetition"          # a river recurs enough to settle into groundwater
    DENSITY = "density"                # a cloud cluster is dense enough to precipitate
    EXTREME_CHARGE = "extreme_charge"  # charge so high the memory must be frozen
    SAFE_CONTEXT = "safe_context"      # environment is safe enough to thaw ice
    REINTEGRATION = "reintegration"    # a filtered memory is ready to rejoin the flow


# Forces that originate outside the engine (driven by the §5.5 signal table).
NATURAL_FORCES: frozenset[Trigger] = frozenset(
    {
        Trigger.HEAT,
        Trigger.PRESSURE,
        Trigger.GRAVITY,
        Trigger.WIND,
        Trigger.TERRAIN,
        Trigger.SALT,
        Trigger.COLD,
        Trigger.STORM,
        Trigger.FILTRATION,
        Trigger.POLLUTION,
    }
)
# Triggers the engine derives from droplet state + lifecycle, not raw input.
SYNTHETIC_TRIGGERS: frozenset[Trigger] = frozenset(t for t in Trigger) - NATURAL_FORCES


@dataclass(frozen=True)
class TriggerConfig:
    """Thresholds used by :func:`detect_triggers` (documented defaults).

    Each ``*_threshold`` is the minimum value (a state float in ``[0, 1]``) at
    which the corresponding trigger fires. ``repetition_cycles`` is a cycle
    *count* (not a unit float).
    """

    heat_threshold: float = 0.6        # temperature OR emotional_charge >= this -> HEAT
    pressure_threshold: float = 0.6    # pressure >= this -> PRESSURE
    gravity_threshold: float = 0.6     # gravity >= this -> GRAVITY
    wind_threshold: float = 0.6        # fluidity (social/communicative movement) -> WIND
    salt_threshold: float = 0.6        # salinity >= this -> SALT
    cold_threshold: float = 0.6        # low temperature (<= 1 - this) -> COLD
    similarity_threshold: float = 0.6  # context similarity score -> SIMILARITY
    density_threshold: float = 0.6     # cluster density (context) -> DENSITY
    extreme_charge_threshold: float = 0.85  # emotional_charge >= this -> EXTREME_CHARGE
    repetition_cycles: int = 3         # cycle_count >= this -> REPETITION
    integrity_threshold: float = 0.85  # high integrity reinforces COLD/preservation


DEFAULT_TRIGGER_CONFIG = TriggerConfig()


def _ctx_flag(context: dict[str, Any], *keys: str) -> bool:
    """True if any of ``keys`` is present and truthy in ``context``."""
    return any(bool(context.get(k)) for k in keys)


def _ctx_float(context: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = context.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def detect_triggers(
    droplet: Droplet,
    context: dict[str, Any] | None = None,
    config: TriggerConfig | None = None,
) -> set[Trigger]:
    """Map a droplet's state + a context dict to the set of fired triggers.

    The mapping (PRD §5.5 plus the synthetic chain) is:

    Natural forces (state-driven, with explicit context overrides):
      * HEAT       -- ``temperature`` or ``emotional_charge`` high, or
                      ``context['attention'|'novelty']`` truthy.
      * PRESSURE   -- ``pressure`` high, or ``context['urgency'|'stakes']``.
      * GRAVITY    -- ``gravity`` high, or ``context['importance'|'identity']``.
      * WIND       -- ``fluidity`` high (communicative movement), or
                      ``context['social'|'communication']``.
      * TERRAIN    -- ``context['platform'|'environment'|'personality']`` present.
      * SALT       -- ``salinity`` high, or ``context['bias'|'symbolic']``.
      * COLD       -- low ``temperature`` *and* very high ``integrity`` together
                      (a quiescent, well-preserved memory), or an explicit
                      ``context['reflection'|'silence'|'distance'|'cold']`` flag.
                      Note: both state conditions are required (AND); a context
                      flag alone also fires it.
      * STORM      -- ``context['crisis'|'conflict'|'rapid_change']`` truthy.
      * FILTRATION -- ``context['verification'|'correction'|'evidence'|'reasoning']``.
      * POLLUTION  -- ``context['misinformation'|'contradiction'|'noise'|'manipulation']``
                      or droplet already flagged contaminated (low purity + meta).

    Synthetic triggers (engine-derived):
      * SIMILARITY    -- ``context['similarity']`` score high (vapor clustering).
      * ASSOCIATION   -- droplet has association links, or ``context['association']``.
      * REPETITION    -- ``cycle.cycle_count`` >= ``repetition_cycles``, or
                         ``context['repetition']``.
      * DENSITY       -- ``context['density'|'cluster_density']`` high.
      * EXTREME_CHARGE-- ``emotional_charge`` >= ``extreme_charge_threshold``.
      * SAFE_CONTEXT  -- ``context['safe_context'|'safe']`` truthy.
      * REINTEGRATION -- ``context['reintegration'|'reintegrate']`` truthy.
    """
    cfg = config or DEFAULT_TRIGGER_CONFIG
    ctx = dict(context or {})
    s = droplet.state
    fired: set[Trigger] = set()

    # --- Natural forces -----------------------------------------------------
    if (
        s.temperature >= cfg.heat_threshold
        or s.emotional_charge >= cfg.heat_threshold
        or _ctx_flag(ctx, "attention", "novelty", "heat")
    ):
        fired.add(Trigger.HEAT)

    if s.pressure >= cfg.pressure_threshold or _ctx_flag(ctx, "urgency", "stakes", "tension"):
        fired.add(Trigger.PRESSURE)

    if s.gravity >= cfg.gravity_threshold or _ctx_flag(ctx, "importance", "identity", "consequence"):
        fired.add(Trigger.GRAVITY)

    if s.fluidity >= cfg.wind_threshold or _ctx_flag(ctx, "social", "communication", "wind"):
        fired.add(Trigger.WIND)

    if _ctx_flag(ctx, "platform", "environment", "personality", "terrain"):
        fired.add(Trigger.TERRAIN)

    if s.salinity >= cfg.salt_threshold or _ctx_flag(ctx, "bias", "symbolic", "salt"):
        fired.add(Trigger.SALT)

    if (
        s.temperature <= (1.0 - cfg.cold_threshold)
        and s.integrity >= cfg.integrity_threshold
    ) or _ctx_flag(ctx, "reflection", "silence", "distance", "cold"):
        fired.add(Trigger.COLD)

    if _ctx_flag(ctx, "crisis", "conflict", "rapid_change", "storm"):
        fired.add(Trigger.STORM)

    if _ctx_flag(ctx, "verification", "correction", "evidence", "reasoning", "therapy", "filtration"):
        fired.add(Trigger.FILTRATION)

    contaminated = bool(droplet.meta.get("requires_filtering")) or droplet.meta.get(
        "usable_for_generation"
    ) is False
    if _ctx_flag(ctx, "misinformation", "contradiction", "noise", "manipulation", "pollution") or contaminated:
        fired.add(Trigger.POLLUTION)

    # --- Synthetic / engine-emitted triggers --------------------------------
    if _ctx_float(ctx, "similarity", "semantic_similarity") >= cfg.similarity_threshold:
        fired.add(Trigger.SIMILARITY)

    if droplet.links.associations or _ctx_flag(ctx, "association", "associations"):
        fired.add(Trigger.ASSOCIATION)

    if droplet.cycle.cycle_count >= cfg.repetition_cycles or _ctx_flag(ctx, "repetition"):
        fired.add(Trigger.REPETITION)

    if _ctx_float(ctx, "density", "cluster_density") >= cfg.density_threshold:
        fired.add(Trigger.DENSITY)

    if s.emotional_charge >= cfg.extreme_charge_threshold or _ctx_flag(ctx, "extreme_charge"):
        fired.add(Trigger.EXTREME_CHARGE)

    if _ctx_flag(ctx, "safe_context", "safe"):
        fired.add(Trigger.SAFE_CONTEXT)

    if _ctx_flag(ctx, "reintegration", "reintegrate"):
        fired.add(Trigger.REINTEGRATION)

    return fired
