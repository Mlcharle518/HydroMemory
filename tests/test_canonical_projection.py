"""Canonical projection for the memory droplet — open-core build (ADR-0047).

In the open core only :class:`~hydromemory.schema.Droplet` is present; the seven upper-layer
object types (Intent / Judgment / Plan / Action / Reflection / Observation / IdentityAnchor)
project in the commercial HydroCognitive build via the same ``to_canonical`` dispatch.
"""
from __future__ import annotations

import pytest

from hydromemory.canonical import ObjectType, to_canonical
from hydromemory.canonical.envelope import CanonicalObject
from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine


@pytest.fixture
def engine(tmp_path):
    eng = build_engine(HydroConfig(db_path=str(tmp_path / "h.db"), vector_dim=64))
    yield eng
    eng.close()


def test_droplet_projects_to_memory_envelope(engine):
    d = engine.verbs.absorb("a memory droplet", source="test")
    env = to_canonical(d)
    assert env.object_type is ObjectType.MEMORY
    assert env.id == d.id
    assert env.confidence == d.state.confidence
    assert env.permissions.visibility in {"private", "shared", "public"}


def test_droplet_envelope_round_trips(engine):
    d = engine.verbs.absorb("round-trip", source="test")
    env = to_canonical(d)
    back = CanonicalObject.from_dict(env.to_dict())
    assert back.to_dict() == env.to_dict()
    assert back.object_type is ObjectType.MEMORY


def test_optional_upper_layer_types_are_none():
    # In the open core, the optional bindings in canonical/projection.py resolve to None
    # because the upper-layer subpackages aren't installed.
    from hydromemory.canonical import projection
    for name in ("Intent", "JudgmentObject", "PlanObject", "ActionObject",
                 "ReflectionObject", "ObservationEvent", "IdentityAnchor"):
        assert getattr(projection, name) is None, f"{name} should be None in the open core"


def test_unmapped_object_raises():
    with pytest.raises(TypeError):
        to_canonical("not a layer object")
