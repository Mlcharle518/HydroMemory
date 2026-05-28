"""Trigger-driven phase transitions (PRD §5.4).

The §5.4 transition chain is transcribed verbatim into a data-driven table of
frozen :class:`TransitionRule` rows::

    Experience -> Liquid          (entry)
    Liquid  + HEAT           -> Vapor
    Vapor   + SIMILARITY     -> Cloud
    Cloud   + DENSITY(+trig) -> Rain
    Rain    + ASSOCIATION    -> River
    River   + REPETITION     -> Groundwater
    Liquid  + EXTREME_CHARGE -> Ice
    Ice     + SAFE_CONTEXT   -> Liquid
    Polluted+ FILTRATION     -> Filtered
    Filtered+ REINTEGRATION  -> Liquid / Groundwater

Each rule carries an optional ``guard(state, context) -> bool`` and an
``effects`` dict of additive deltas applied to the droplet's state floats (then
clamped to ``[0, 1]``). :func:`apply_phase_transition` looks up the row matching
``(droplet.phase, trigger)`` whose guard passes, mutates the phase, applies the
effects, and stamps ``cycle.last_transformed``.

Numeric thresholds used by the guards live in :class:`PhaseConfig` (documented
defaults). The "Experience -> Liquid" entry is modelled by
:func:`assign_initial_phase` (no source phase to match on).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hydromemory.schema import Droplet, Phase, State, clamp_unit
from hydromemory.triggers import Trigger

# A guard reads the droplet state + the (free-form) transition context.
Guard = Callable[[State, Mapping[str, object]], bool]


@dataclass(frozen=True)
class PhaseConfig:
    """Thresholds read by the transition guards (documented defaults).

    * ``density_threshold`` -- cloud needs ``context['density']`` (or
      ``cluster_density``) >= this to precipitate into rain.
    * ``extreme_charge_threshold`` -- liquid freezes to ice only when
      ``emotional_charge`` >= this (mirrors the EXTREME_CHARGE trigger).
    * ``repetition_cycles`` -- a river settles into groundwater once
      ``cycle.cycle_count`` >= this.
    * ``groundwater_gravity_threshold`` -- a reintegrated FILTERED droplet sinks
      to GROUNDWATER (rather than LIQUID) when ``gravity`` >= this (identity
      relevance); otherwise it returns to LIQUID.
    """

    density_threshold: float = 0.6
    extreme_charge_threshold: float = 0.85
    repetition_cycles: int = 3
    groundwater_gravity_threshold: float = 0.7


DEFAULT_PHASE_CONFIG = PhaseConfig()


@dataclass(frozen=True)
class TransitionRule:
    """One row of the §5.4 transition table."""

    from_phase: Phase
    trigger: Trigger
    to_phase: Phase
    guard: Guard | None = None
    effects: Mapping[str, float] = field(default_factory=dict)
    # Optional dynamic target: overrides ``to_phase`` when the guard alone is
    # insufficient to pick the destination (used for FILTERED + REINTEGRATION,
    # which goes to LIQUID *or* GROUNDWATER depending on gravity).
    target_fn: Callable[[State, Mapping[str, object], PhaseConfig], Phase] | None = None


def _density_ok(config: PhaseConfig) -> Guard:
    def guard(state: State, ctx: Mapping[str, object]) -> bool:
        density = _as_float(ctx.get("density"))
        if density is None:
            density = _as_float(ctx.get("cluster_density"))
        return (density or 0.0) >= config.density_threshold

    return guard


def _extreme_charge_ok(config: PhaseConfig) -> Guard:
    def guard(state: State, ctx: Mapping[str, object]) -> bool:
        return state.emotional_charge >= config.extreme_charge_threshold

    return guard


def _repetition_ok(config: PhaseConfig) -> Guard:
    def guard(state: State, ctx: Mapping[str, object]) -> bool:
        count = _as_float(ctx.get("cycle_count"))
        return count is not None and count >= config.repetition_cycles

    return guard


def _safe_context_ok(state: State, ctx: Mapping[str, object]) -> bool:
    return bool(ctx.get("safe_context")) or bool(ctx.get("safe"))


def _reintegration_target(config: PhaseConfig) -> Callable[[State, Mapping[str, object], PhaseConfig], Phase]:
    def target(state: State, ctx: Mapping[str, object], cfg: PhaseConfig) -> Phase:
        # Identity-relevant (high gravity) memory settles deep; else rejoins flow.
        if state.gravity >= cfg.groundwater_gravity_threshold:
            return Phase.GROUNDWATER
        return Phase.LIQUID

    return target


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def build_transition_table(config: PhaseConfig | None = None) -> tuple[TransitionRule, ...]:
    """Build the §5.4 transition table (guards bound to ``config``)."""
    cfg = config or DEFAULT_PHASE_CONFIG
    return (
        # Liquid + HEAT -> Vapor : abstraction raises temperature, loosens form.
        TransitionRule(
            from_phase=Phase.LIQUID,
            trigger=Trigger.HEAT,
            to_phase=Phase.VAPOR,
            effects={"temperature": 0.2, "fluidity": 0.1, "depth": -0.1},
        ),
        # Vapor + SIMILARITY -> Cloud : like abstractions cluster together.
        TransitionRule(
            from_phase=Phase.VAPOR,
            trigger=Trigger.SIMILARITY,
            to_phase=Phase.CLOUD,
            effects={"pressure": 0.1, "confidence": 0.05},
        ),
        # Cloud + DENSITY (+trigger) -> Rain : a dense cluster precipitates.
        TransitionRule(
            from_phase=Phase.CLOUD,
            trigger=Trigger.DENSITY,
            to_phase=Phase.RAIN,
            guard=_density_ok(cfg),
            effects={"temperature": -0.2, "pressure": -0.1, "fluidity": 0.1},
        ),
        # Rain + ASSOCIATION -> River : recalled memory joins an associative chain.
        TransitionRule(
            from_phase=Phase.RAIN,
            trigger=Trigger.ASSOCIATION,
            to_phase=Phase.RIVER,
            effects={"fluidity": 0.1, "gravity": 0.05},
        ),
        # River + REPETITION -> Groundwater : repeated flow settles into depth.
        TransitionRule(
            from_phase=Phase.RIVER,
            trigger=Trigger.REPETITION,
            to_phase=Phase.GROUNDWATER,
            guard=_repetition_ok(cfg),
            effects={"depth": 0.3, "gravity": 0.1, "temperature": -0.2, "fluidity": -0.2},
        ),
        # Liquid + EXTREME_CHARGE -> Ice : an overcharged memory is frozen intact.
        TransitionRule(
            from_phase=Phase.LIQUID,
            trigger=Trigger.EXTREME_CHARGE,
            to_phase=Phase.ICE,
            guard=_extreme_charge_ok(cfg),
            effects={"temperature": -0.4, "integrity": 0.2, "fluidity": -0.5},
        ),
        # Ice + SAFE_CONTEXT -> Liquid : a safe environment thaws the snapshot.
        TransitionRule(
            from_phase=Phase.ICE,
            trigger=Trigger.SAFE_CONTEXT,
            to_phase=Phase.LIQUID,
            guard=_safe_context_ok,
            effects={"temperature": 0.3, "fluidity": 0.4},
        ),
        # Polluted + FILTRATION -> Filtered : verification cleans the memory.
        TransitionRule(
            from_phase=Phase.POLLUTED,
            trigger=Trigger.FILTRATION,
            to_phase=Phase.FILTERED,
            effects={"purity": 0.4, "confidence": 0.1, "salinity": -0.2},
        ),
        # Filtered + REINTEGRATION -> Liquid / Groundwater (dynamic target).
        TransitionRule(
            from_phase=Phase.FILTERED,
            trigger=Trigger.REINTEGRATION,
            to_phase=Phase.LIQUID,
            target_fn=_reintegration_target(cfg),
            effects={"fluidity": 0.2, "purity": 0.05},
        ),
    )


def find_rule(
    phase: Phase,
    trigger: Trigger,
    state: State,
    context: Mapping[str, object],
    config: PhaseConfig | None = None,
) -> TransitionRule | None:
    """Return the first table row matching ``(phase, trigger)`` whose guard passes."""
    ctx = dict(context or {})
    for rule in build_transition_table(config):
        if rule.from_phase is phase and rule.trigger is trigger:
            if rule.guard is None or rule.guard(state, ctx):
                return rule
    return None


# Order in which simultaneously-fired triggers are applied to a droplet.
# Protective/structural transitions take precedence over generic forces so a
# fresh LIQUID droplet with both EXTREME_CHARGE and HEAT freezes to ICE (the
# protective outcome) rather than merely evaporating to VAPOR. Triggers absent
# from this list are applied last (in enum order, deterministically).
TRIGGER_PRIORITY: tuple[Trigger, ...] = (
    Trigger.POLLUTION,        # contaminated input must be flagged first
    Trigger.FILTRATION,       # polluted -> filtered
    Trigger.REINTEGRATION,    # filtered -> liquid/groundwater
    Trigger.EXTREME_CHARGE,   # liquid -> ice (protective snapshot)
    Trigger.SAFE_CONTEXT,     # ice -> liquid (thaw)
    Trigger.HEAT,             # liquid -> vapor
    Trigger.SIMILARITY,       # vapor -> cloud
    Trigger.DENSITY,          # cloud -> rain
    Trigger.ASSOCIATION,      # rain -> river
    Trigger.REPETITION,       # river -> groundwater
)


def _ordered_triggers(triggers: Iterable[Trigger]) -> list[Trigger]:
    """Order ``triggers`` by :data:`TRIGGER_PRIORITY` (unknowns last, enum order)."""
    fired = set(triggers)
    ordered = [t for t in TRIGGER_PRIORITY if t in fired]
    rest = sorted((t for t in fired if t not in TRIGGER_PRIORITY), key=lambda t: t.value)
    return ordered + rest


def assign_initial_phase(droplet: Droplet) -> Droplet:
    """Model the ``Experience -> Liquid`` entry: a fresh droplet is LIQUID."""
    droplet.phase = Phase.LIQUID
    return droplet


def apply_phase_transitions(
    droplet: Droplet,
    triggers: Iterable[Trigger],
    context: Mapping[str, object] | None = None,
    config: PhaseConfig | None = None,
) -> Droplet:
    """Apply a *set* of fired triggers in priority order (the §14 transform step).

    Triggers are sorted by :data:`TRIGGER_PRIORITY` so protective transitions
    win, then applied one at a time; each may advance the droplet along the §5.4
    chain. Returns the (mutated) droplet.
    """
    for trigger in _ordered_triggers(triggers):
        apply_phase_transition(droplet, trigger, context, config)
    return droplet


def _apply_effects(state: State, effects: Mapping[str, float]) -> None:
    for field_name, delta in effects.items():
        current = getattr(state, field_name)
        setattr(state, field_name, clamp_unit(current + delta))


def apply_phase_transition(
    droplet: Droplet,
    trigger: Trigger,
    context: Mapping[str, object] | None = None,
    config: PhaseConfig | None = None,
) -> Droplet:
    """Apply the §5.4 transition for ``(droplet.phase, trigger)`` if one fires.

    On a match: mutate ``droplet.phase`` (honouring a ``target_fn`` for dynamic
    destinations), apply the rule's additive ``effects`` (clamped to ``[0, 1]``),
    and stamp ``cycle.last_transformed``. If no rule matches (wrong phase, guard
    blocks, or unrelated trigger), the droplet is returned unchanged.
    """
    cfg = config or DEFAULT_PHASE_CONFIG
    ctx = dict(context or {})
    rule = find_rule(droplet.phase, trigger, droplet.state, ctx, cfg)
    if rule is None:
        return droplet

    if rule.target_fn is not None:
        droplet.phase = rule.target_fn(droplet.state, ctx, cfg)
    else:
        droplet.phase = rule.to_phase

    _apply_effects(droplet.state, rule.effects)
    droplet.cycle.last_transformed = datetime.now(UTC)
    return droplet
