"""Track B: trigger detection (PRD §5.5).

Covers each natural force and each synthetic trigger -- fired from state floats
and/or from context signals -- plus the enum partition invariants.
"""
from __future__ import annotations

from hydromemory.schema import Droplet, State
from hydromemory.triggers import (
    NATURAL_FORCES,
    SYNTHETIC_TRIGGERS,
    Trigger,
    TriggerConfig,
    detect_triggers,
)


def _d(**state: float) -> Droplet:
    return Droplet(id="d", state=State(**state))


# --- Enum partition ---------------------------------------------------------
def test_trigger_enum_partition():
    assert len(NATURAL_FORCES) == 10
    assert len(SYNTHETIC_TRIGGERS) == 7
    assert NATURAL_FORCES.isdisjoint(SYNTHETIC_TRIGGERS)
    assert NATURAL_FORCES | SYNTHETIC_TRIGGERS == set(Trigger)


# --- Natural forces from state ---------------------------------------------
def test_heat_from_temperature():
    assert Trigger.HEAT in detect_triggers(_d(temperature=0.8), {})


def test_heat_from_emotional_charge():
    assert Trigger.HEAT in detect_triggers(_d(emotional_charge=0.7), {})


def test_pressure_from_state():
    assert Trigger.PRESSURE in detect_triggers(_d(pressure=0.8), {})


def test_gravity_from_state():
    assert Trigger.GRAVITY in detect_triggers(_d(gravity=0.8), {})


def test_wind_from_fluidity():
    assert Trigger.WIND in detect_triggers(_d(fluidity=0.8), {})


def test_salt_from_salinity():
    assert Trigger.SALT in detect_triggers(_d(salinity=0.8), {})


def test_cold_from_low_temp_high_integrity():
    # low temperature + high integrity -> preservation/COLD
    assert Trigger.COLD in detect_triggers(_d(temperature=0.1, integrity=0.95), {})


def test_extreme_charge_from_state():
    fired = detect_triggers(_d(emotional_charge=0.9), {})
    assert Trigger.EXTREME_CHARGE in fired
    assert Trigger.HEAT in fired  # also crosses the heat threshold


# --- Natural forces from context -------------------------------------------
def test_heat_from_context():
    assert Trigger.HEAT in detect_triggers(_d(), {"novelty": True})


def test_terrain_from_context():
    assert Trigger.TERRAIN in detect_triggers(_d(), {"platform": "web"})


def test_storm_from_crisis_context():
    assert Trigger.STORM in detect_triggers(_d(), {"crisis": True})


def test_filtration_from_context():
    assert Trigger.FILTRATION in detect_triggers(_d(), {"verification": True})


def test_pollution_from_context():
    assert Trigger.POLLUTION in detect_triggers(_d(), {"contradiction": True})


def test_pollution_from_contaminated_meta():
    d = _d()
    d.meta["requires_filtering"] = True
    assert Trigger.POLLUTION in detect_triggers(d, {})


# --- Synthetic triggers -----------------------------------------------------
def test_similarity_from_context_score():
    assert Trigger.SIMILARITY in detect_triggers(_d(), {"similarity": 0.8})


def test_association_from_links():
    d = _d()
    d.links.associations.append("other")
    assert Trigger.ASSOCIATION in detect_triggers(d, {})


def test_repetition_from_cycle_count():
    d = _d()
    d.cycle.cycle_count = 5
    assert Trigger.REPETITION in detect_triggers(d, {})


def test_density_from_context():
    assert Trigger.DENSITY in detect_triggers(_d(), {"density": 0.9})


def test_safe_context_trigger():
    assert Trigger.SAFE_CONTEXT in detect_triggers(_d(), {"safe_context": True})


def test_reintegration_trigger():
    assert Trigger.REINTEGRATION in detect_triggers(_d(), {"reintegration": True})


# --- Negative / threshold cases --------------------------------------------
def test_no_triggers_for_neutral_droplet():
    assert detect_triggers(_d(), {}) == set()


def test_custom_threshold_config():
    cfg = TriggerConfig(heat_threshold=0.95)
    # 0.8 < 0.95 -> HEAT does not fire under the stricter config
    assert Trigger.HEAT not in detect_triggers(_d(temperature=0.8), {}, cfg)


def test_repetition_custom_cycles():
    cfg = TriggerConfig(repetition_cycles=10)
    d = _d()
    d.cycle.cycle_count = 5
    assert Trigger.REPETITION not in detect_triggers(d, {}, cfg)
