"""Track B: phase transition engine (PRD §5.4).

Covers every row of the transition table -- each rule fires on the right
``(phase, trigger)`` pair, each guard *blocks* when its condition is unmet, and
effects/cycle stamping behave as specified.
"""
from __future__ import annotations

from hydromemory.phases import (
    PhaseConfig,
    apply_phase_transition,
    assign_initial_phase,
    build_transition_table,
    find_rule,
)
from hydromemory.schema import Droplet, Phase, State
from hydromemory.triggers import Trigger


def _d(phase: Phase, **state: float) -> Droplet:
    return Droplet(id="d", phase=phase, state=State(**state))


# --- Entry rule -------------------------------------------------------------
def test_experience_to_liquid_entry():
    d = Droplet(id="x", phase=Phase.VAPOR)
    assign_initial_phase(d)
    assert d.phase is Phase.LIQUID


# --- Each rule FIRES --------------------------------------------------------
def test_liquid_heat_to_vapor():
    d = _d(Phase.LIQUID, temperature=0.4)
    apply_phase_transition(d, Trigger.HEAT, {})
    assert d.phase is Phase.VAPOR
    assert d.cycle.last_transformed is not None


def test_vapor_similarity_to_cloud():
    d = _d(Phase.VAPOR)
    apply_phase_transition(d, Trigger.SIMILARITY, {})
    assert d.phase is Phase.CLOUD


def test_cloud_density_to_rain_fires_with_high_density():
    d = _d(Phase.CLOUD)
    apply_phase_transition(d, Trigger.DENSITY, {"density": 0.9})
    assert d.phase is Phase.RAIN


def test_rain_association_to_river():
    d = _d(Phase.RAIN)
    apply_phase_transition(d, Trigger.ASSOCIATION, {})
    assert d.phase is Phase.RIVER


def test_river_repetition_to_groundwater_fires_with_enough_cycles():
    d = _d(Phase.RIVER)
    apply_phase_transition(d, Trigger.REPETITION, {"cycle_count": 5})
    assert d.phase is Phase.GROUNDWATER
    assert d.state.depth > 0.0  # depth effect applied


def test_liquid_extreme_charge_to_ice_fires_when_charge_high():
    d = _d(Phase.LIQUID, emotional_charge=0.9)
    apply_phase_transition(d, Trigger.EXTREME_CHARGE, {})
    assert d.phase is Phase.ICE
    assert d.state.integrity > 0.0


def test_ice_safe_context_to_liquid_fires_when_safe():
    d = _d(Phase.ICE)
    apply_phase_transition(d, Trigger.SAFE_CONTEXT, {"safe_context": True})
    assert d.phase is Phase.LIQUID


def test_polluted_filtration_to_filtered():
    d = _d(Phase.POLLUTED, purity=0.2)
    apply_phase_transition(d, Trigger.FILTRATION, {})
    assert d.phase is Phase.FILTERED
    assert d.state.purity > 0.2  # purity raised


def test_filtered_reintegration_to_liquid_or_groundwater():
    high = _d(Phase.FILTERED, gravity=0.95)
    apply_phase_transition(high, Trigger.REINTEGRATION, {})
    assert high.phase is Phase.GROUNDWATER

    low = _d(Phase.FILTERED, gravity=0.1)
    apply_phase_transition(low, Trigger.REINTEGRATION, {})
    assert low.phase is Phase.LIQUID


# --- Each guard BLOCKS ------------------------------------------------------
def test_cloud_density_blocked_when_density_low():
    d = _d(Phase.CLOUD)
    apply_phase_transition(d, Trigger.DENSITY, {"density": 0.1})
    assert d.phase is Phase.CLOUD  # unchanged
    assert d.cycle.last_transformed is None


def test_river_repetition_blocked_without_enough_cycles():
    d = _d(Phase.RIVER)
    apply_phase_transition(d, Trigger.REPETITION, {"cycle_count": 1})
    assert d.phase is Phase.RIVER


def test_liquid_extreme_charge_blocked_when_charge_low():
    d = _d(Phase.LIQUID, emotional_charge=0.2)
    apply_phase_transition(d, Trigger.EXTREME_CHARGE, {})
    assert d.phase is Phase.LIQUID


def test_ice_safe_context_blocked_when_not_safe():
    d = _d(Phase.ICE)
    apply_phase_transition(d, Trigger.SAFE_CONTEXT, {"safe_context": False})
    assert d.phase is Phase.ICE


# --- Non-matching trigger / phase is a no-op --------------------------------
def test_unrelated_trigger_is_noop():
    d = _d(Phase.LIQUID, temperature=0.1)
    before = d.phase
    apply_phase_transition(d, Trigger.WIND, {})  # no LIQUID+WIND rule
    assert d.phase is before
    assert d.cycle.last_transformed is None


def test_wrong_phase_for_trigger_is_noop():
    d = _d(Phase.GROUNDWATER)
    apply_phase_transition(d, Trigger.HEAT, {})  # HEAT only applies to LIQUID
    assert d.phase is Phase.GROUNDWATER


# --- Table structure --------------------------------------------------------
def test_transition_table_has_nine_rules():
    table = build_transition_table()
    assert len(table) == 9
    pairs = {(r.from_phase, r.trigger) for r in table}
    assert (Phase.LIQUID, Trigger.HEAT) in pairs
    assert (Phase.FILTERED, Trigger.REINTEGRATION) in pairs


def test_effects_are_clamped_to_unit_interval():
    d = _d(Phase.LIQUID, temperature=0.95)
    apply_phase_transition(d, Trigger.HEAT, {})  # +0.2 temperature -> clamps to 1.0
    assert d.state.temperature == 1.0


def test_custom_phase_config_threshold():
    cfg = PhaseConfig(density_threshold=0.95)
    d = _d(Phase.CLOUD)
    # density 0.9 < 0.95 threshold -> blocked
    apply_phase_transition(d, Trigger.DENSITY, {"density": 0.9}, cfg)
    assert d.phase is Phase.CLOUD


def test_find_rule_returns_none_for_unmatched():
    assert find_rule(Phase.OCEAN, Trigger.HEAT, State(), {}) is None
