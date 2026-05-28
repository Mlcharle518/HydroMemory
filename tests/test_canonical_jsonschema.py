"""JSON Schema export for the canonical §8 envelope (Master Spec §25).

Covers: the hand-written envelope schema is structurally valid-ish and its ``object_type`` enum
carries all nine canonical types; a real projected object validates clean; a malformed dict yields
errors (missing id, bad object_type, out-of-range confidence, bad visibility); the per-type schema
pins ``object_type`` to a ``const``; and :func:`export` writes one parseable JSON file per schema.
"""
from __future__ import annotations

import json

import pytest

from hydromemory.canonical.envelope import CanonicalObject, ObjectType
from hydromemory.canonical.jsonschema import (
    ALL_SCHEMAS,
    ENVELOPE_SCHEMA,
    export,
    object_type_schema,
    validate,
)
from hydromemory.canonical.projection import to_canonical
from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine


@pytest.fixture
def engine(tmp_path):
    eng = build_engine(HydroConfig(db_path=str(tmp_path / "h.db"), vector_dim=64))
    yield eng
    eng.close()


def test_envelope_schema_shape():
    assert ENVELOPE_SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert ENVELOPE_SCHEMA["type"] == "object"
    assert "title" in ENVELOPE_SCHEMA
    assert isinstance(ENVELOPE_SCHEMA["properties"], dict)
    assert isinstance(ENVELOPE_SCHEMA["required"], list)
    # Every required key has a matching property definition.
    for key in ENVELOPE_SCHEMA["required"]:
        assert key in ENVELOPE_SCHEMA["properties"]


def test_object_type_enum_has_all_nine_values():
    enum = ENVELOPE_SCHEMA["properties"]["object_type"]["enum"]
    assert len(enum) == 9
    assert set(enum) == {t.value for t in ObjectType}


def test_visibility_enum_derived_from_visibilities():
    visibility = ENVELOPE_SCHEMA["properties"]["permissions"]["properties"]["visibility"]
    assert set(visibility["enum"]) == {"private", "shared", "public"}


def test_valid_projected_object_validates_clean(engine):
    # In the open core only the memory droplet is projectable; use it to exercise the validator
    # against a real projected object.
    d = engine.verbs.absorb("a memory droplet", source="test")
    obj_dict = to_canonical(d).to_dict()
    assert validate(obj_dict) == []
    # And against the type-specialized schema too.
    assert validate(obj_dict, object_type=ObjectType.MEMORY) == []


def test_directly_constructed_object_validates_clean():
    obj = CanonicalObject(id="mem_1", object_type=ObjectType.MEMORY, confidence=0.5, sensitivity=0.2)
    assert validate(obj.to_dict()) == []


def test_missing_id_yields_error():
    obj = CanonicalObject(id="x", object_type=ObjectType.MEMORY).to_dict()
    del obj["id"]
    errors = validate(obj)
    assert errors
    assert any("id" in e for e in errors)


def test_bad_object_type_yields_error():
    obj = CanonicalObject(id="x", object_type=ObjectType.MEMORY).to_dict()
    obj["object_type"] = "not_a_real_type"
    errors = validate(obj)
    assert errors
    assert any("object_type" in e for e in errors)


def test_out_of_range_confidence_yields_error():
    obj = CanonicalObject(id="x", object_type=ObjectType.MEMORY).to_dict()
    obj["confidence"] = 1.5  # bypasses the dataclass clamp by editing the raw dict
    errors = validate(obj)
    assert errors
    assert any("confidence" in e for e in errors)


def test_bad_visibility_yields_error():
    obj = CanonicalObject(id="x", object_type=ObjectType.MEMORY).to_dict()
    obj["permissions"]["visibility"] = "top_secret"
    errors = validate(obj)
    assert errors
    assert any("visibility" in e for e in errors)


def test_multiple_problems_all_reported():
    obj = CanonicalObject(id="x", object_type=ObjectType.MEMORY).to_dict()
    del obj["id"]
    obj["object_type"] = "bogus"
    obj["confidence"] = 2.0
    obj["permissions"]["visibility"] = "nope"
    errors = validate(obj)
    # Several independent problems should surface, not just the first.
    assert len(errors) >= 3


def test_object_type_schema_pins_const_for_intent():
    schema = object_type_schema(ObjectType.INTENT)
    assert schema["properties"]["object_type"] == {"type": "string", "const": "intent"}
    # A mismatching object_type must fail against the specialized schema.
    obj = CanonicalObject(id="x", object_type=ObjectType.MEMORY).to_dict()
    errors = validate(obj, object_type=ObjectType.INTENT)
    assert any("object_type" in e for e in errors)


def test_object_type_schema_accepts_string_value():
    schema = object_type_schema("plan")
    assert schema["properties"]["object_type"]["const"] == "plan"


def test_all_schemas_has_envelope_and_every_type():
    assert "envelope" in ALL_SCHEMAS
    for object_type in ObjectType:
        assert object_type.value in ALL_SCHEMAS
    assert len(ALL_SCHEMAS) == 1 + len(ObjectType)


def test_specializing_does_not_mutate_envelope_schema():
    before = ENVELOPE_SCHEMA["properties"]["object_type"]["enum"]
    object_type_schema(ObjectType.ACTION)
    after = ENVELOPE_SCHEMA["properties"]["object_type"]["enum"]
    # The shared envelope schema must still expose the full enum, not a const.
    assert before == after
    assert "const" not in ENVELOPE_SCHEMA["properties"]["object_type"]


def test_export_writes_expected_files_as_valid_json(tmp_path):
    written = export(str(tmp_path))
    assert len(written) == 1 + len(ObjectType)
    expected_envelope = str(tmp_path / "envelope.schema.json")
    assert expected_envelope in written
    for object_type in ObjectType:
        assert str(tmp_path / f"{object_type.value}.schema.json") in written
    # Every written file is parseable JSON carrying the draft dialect.
    for path in written:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        assert loaded["$schema"] == "https://json-schema.org/draft/2020-12/schema"
