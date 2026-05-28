"""Phase 0 Done gate: the frozen contracts admit the spec's own data.

Proves that the schema + protocol envelope round-trip the PRD's JSON blobs with
zero data loss, and that the enum/reservoir invariants match §7/§5.4/§10.
"""
from __future__ import annotations

from hydromemory.protocol import ProtocolEnvelope
from hydromemory.reservoirs import Reservoir, normalize_reservoir
from hydromemory.schema import (
    CANONICAL_STATE_FIELDS,
    STORABLE_PHASES,
    TRANSIENT_PHASES,
    Droplet,
    Phase,
    Retention,
    Visibility,
)


def test_droplet_blobs_round_trip_idempotently(spec_droplet_blobs):
    for name, blob in spec_droplet_blobs.items():
        first = Droplet.from_dict(blob)
        reparsed = Droplet.from_dict(first.to_dict())
        assert first == reparsed, f"round-trip not idempotent for {name}"


def test_droplet_5_2_fields(spec_droplet_blobs):
    d = Droplet.from_dict(spec_droplet_blobs["droplet_5_2"])
    assert d.id == "mem_9f31"
    assert d.memory_type == "conceptual_preference"
    assert d.phase is Phase.LIQUID
    assert d.reservoir is Reservoir.WORKING_STREAM
    assert d.semantic_tags == ["AI memory", "thinking style", "personalization", "architecture"]
    # emotional_charge present (§5.2); gravity absent -> defaults to 0.0.
    assert d.state.emotional_charge == 0.58
    assert d.state.gravity == 0.0
    assert d.state.purity == 0.91
    # permission aliases resolved: scope -> owner/visibility, agent_access -> allowed_agents.
    assert d.permissions.owner == "user"
    assert d.permissions.visibility is Visibility.PRIVATE
    assert d.permissions.allowed_agents == ["personal_assistant", "reasoning_agent"]
    assert d.permissions.retention is Retention.PERSISTENT
    assert d.permissions.requires_consent_for_external_use is True


def test_example_a_loose_shape(spec_droplet_blobs):
    d = Droplet.from_dict(spec_droplet_blobs["example_a"])
    assert d.content == "I was dismissed during a meeting."
    assert d.phase is Phase.LIQUID
    # list-valued "context" becomes semantic_tags.
    assert d.semantic_tags == ["work", "authority", "public speaking"]
    # top-level "charge"/"pressure" fold into the state vector.
    assert d.state.emotional_charge == 0.68
    assert d.state.pressure == 0.55
    # no id supplied -> one is generated.
    assert d.id.startswith("mem_")
    # reservoir defaults to working_stream.
    assert d.reservoir is Reservoir.WORKING_STREAM


def test_example_f_updated_memory(spec_droplet_blobs):
    d = Droplet.from_dict(spec_droplet_blobs["example_f"])
    assert d.phase is Phase.FILTERED
    assert d.state.purity == 0.92


def test_contamination_blob_preserves_extra_keys(spec_droplet_blobs):
    d = Droplet.from_dict(spec_droplet_blobs["contamination_10_1"])
    assert d.id == "mem_7712"
    assert d.phase is Phase.POLLUTED
    assert d.reservoir is Reservoir.CONTAMINATED  # contaminated_pool alias normalized
    # unknown keys are preserved (zero data loss), not dropped.
    assert d.meta["reason"].startswith("Low confidence")
    assert d.meta["usable_for_generation"] is False
    assert d.meta["requires_filtering"] is True


def test_protocol_envelope_round_trips(envelope_blob):
    env = ProtocolEnvelope.from_dict(envelope_blob)
    assert env == ProtocolEnvelope.from_dict(env.to_dict())
    assert env.operation == "ABSORB"
    assert env.classification["memory_type"] == "cognitive_style"
    # the envelope's initial_state preserves the raw spec reservoir alias.
    assert env.initial_state["reservoir"] == "surface_reservoir"
    assert env.input["context"] == {"topic": "AI memory systems", "session_type": "design"}


def test_phase_enum_invariants():
    assert len(Phase) == 13  # §5.4 full set
    assert len(STORABLE_PHASES) == 9  # §7 persisted subset
    assert len(TRANSIENT_PHASES) == 4
    assert {p.value for p in TRANSIENT_PHASES} == {"river", "snow", "fog", "steam"}


def test_canonical_state_fields_match_section_7():
    assert CANONICAL_STATE_FIELDS == (
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


def test_reservoir_alias_normalization():
    assert normalize_reservoir("surface_reservoir") is Reservoir.SURFACE
    assert normalize_reservoir("cloud_layer") is Reservoir.CLOUD
    assert normalize_reservoir("contaminated_pool") is Reservoir.CONTAMINATED
    assert normalize_reservoir("sacred_spring") is Reservoir.SACRED
    assert normalize_reservoir("stream") is Reservoir.WORKING_STREAM
    assert normalize_reservoir(Reservoir.GLACIER) is Reservoir.GLACIER
