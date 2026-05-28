"""JSON Schema (draft 2020-12) export for the canonical §8 envelope (Master Spec §25).

Master Spec §25 ("Canonical JSON schemas for all object types") asks that every HydroCognitive
object be describable by a published JSON Schema so external tools and SDKs can validate objects
without importing the Python dataclasses. This module emits one hand-written schema for the §8
minimum-shared-metadata envelope (:class:`~hydromemory.canonical.envelope.CanonicalObject`), plus a
specialized per-type schema for each of the nine :class:`~hydromemory.canonical.envelope.ObjectType`
values (the ``object_type`` field is pinned to a ``const``).

The validated shape is always the *envelope* — the layer-specific body is never carried on it (see
:mod:`hydromemory.canonical.projection`). Enum vocabularies (the nine object types, the three
visibilities) are derived programmatically from :class:`ObjectType` / :data:`VISIBILITIES` so the
schemas can never drift from the dataclasses.

The :func:`validate` helper uses the optional ``jsonschema`` library when it is installed; otherwise
it falls back to a small, dependency-free structural validator (required keys, types, enum
membership, and the [0, 1] numeric range for ``confidence`` / ``sensitivity``). No new runtime
dependency is introduced either way.
"""
from __future__ import annotations

import json
import os
from typing import Any

from hydromemory.canonical.envelope import VISIBILITIES, ObjectType

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Enum vocabularies, derived from the canonical dataclasses so they cannot drift (Master Spec §7/§8).
_OBJECT_TYPE_VALUES: list[str] = [t.value for t in ObjectType]
_VISIBILITY_VALUES: list[str] = sorted(VISIBILITIES)


def _build_envelope_schema() -> dict[str, Any]:
    """Construct the draft-2020-12 schema for the §8 envelope (see :data:`ENVELOPE_SCHEMA`)."""
    return {
        "$schema": _SCHEMA_DIALECT,
        "title": "HydroCognitive Canonical Object (envelope)",
        "description": (
            "Master Spec §8 minimum-shared-metadata envelope every HydroCognitive object projects "
            "onto. Carries routing/gating/audit metadata only, never the layer-specific body."
        ),
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "object_type": {"type": "string", "enum": list(_OBJECT_TYPE_VALUES)},
            "source": {"type": "string"},
            "created_at": {"type": ["string", "null"], "format": "date-time"},
            "owner": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "sensitivity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "permissions": {
                "type": "object",
                "properties": {
                    "visibility": {"type": "string", "enum": list(_VISIBILITY_VALUES)},
                    "allowed_agents": {"type": "array", "items": {"type": "string"}},
                    "external_sharing": {"type": "boolean"},
                    "requires_user_review": {"type": "boolean"},
                },
                "required": [
                    "visibility",
                    "allowed_agents",
                    "external_sharing",
                    "requires_user_review",
                ],
            },
            "links": {
                "type": "object",
                "properties": {
                    "derived_from": {"type": "array", "items": {"type": "string"}},
                    "supports": {"type": "array", "items": {"type": "string"}},
                    "contradicts": {"type": "array", "items": {"type": "string"}},
                    "supersedes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["derived_from", "supports", "contradicts", "supersedes"],
            },
            "audit": {
                "type": "object",
                "properties": {
                    "created_by": {"type": "string"},
                    "last_updated": {"type": ["string", "null"], "format": "date-time"},
                    "rollback_ref": {"type": ["string", "null"]},
                },
                "required": ["created_by", "last_updated", "rollback_ref"],
            },
        },
        "required": [
            "id",
            "object_type",
            "source",
            "created_at",
            "owner",
            "confidence",
            "sensitivity",
            "permissions",
            "links",
            "audit",
        ],
    }


ENVELOPE_SCHEMA: dict[str, Any] = _build_envelope_schema()


def object_type_schema(object_type: ObjectType | str) -> dict[str, Any]:
    """Return the envelope schema specialized so ``object_type`` is ``const`` *object_type*.

    Accepts an :class:`ObjectType` or its string value; raises :class:`ValueError` for an unknown
    string (via :class:`ObjectType`'s own coercion). The returned schema is a deep, independent copy
    of :data:`ENVELOPE_SCHEMA` — mutating it never affects the shared envelope schema.
    """
    value = ObjectType(object_type).value if not isinstance(object_type, ObjectType) else object_type.value
    schema = _build_envelope_schema()
    schema["title"] = f"HydroCognitive Canonical Object ({value})"
    schema["description"] = (
        f"Master Spec §8 envelope specialized to the '{value}' object type "
        "(object_type pinned to a const)."
    )
    # Pin object_type to this single value (const) while keeping the string type constraint.
    schema["properties"]["object_type"] = {"type": "string", "const": value}
    return schema


def _build_all_schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {"envelope": ENVELOPE_SCHEMA}
    for object_type in ObjectType:
        schemas[object_type.value] = object_type_schema(object_type)
    return schemas


ALL_SCHEMAS: dict[str, dict[str, Any]] = _build_all_schemas()


# --------------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------------
def _select_schema(object_type: ObjectType | str | None) -> dict[str, Any]:
    """Pick the per-type schema when *object_type* is given, else the generic envelope schema."""
    if object_type is None:
        return ENVELOPE_SCHEMA
    return object_type_schema(object_type)


def _has_jsonschema() -> bool:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return False
    return True


def _validate_with_jsonschema(obj_dict: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    import jsonschema  # local import: optional dependency

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(obj_dict), key=lambda e: list(e.path))
    messages: list[str] = []
    for err in errors:
        location = "/".join(str(p) for p in err.path) or "<root>"
        messages.append(f"{location}: {err.message}")
    return messages


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    py_type = _JSON_TYPES[expected]
    # JSON booleans are not numbers (Python's bool is an int subclass — exclude it explicitly).
    if expected in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, py_type)


def _check_type(value: Any, type_spec: Any, location: str, errors: list[str]) -> bool:
    """Validate *value* against a JSON Schema ``type`` (string or list of strings)."""
    expected = type_spec if isinstance(type_spec, list) else [type_spec]
    if any(_type_matches(value, t) for t in expected):
        return True
    errors.append(f"{location}: expected type {' | '.join(expected)}, got {type(value).__name__}")
    return False


def _validate_node(value: Any, schema: dict[str, Any], location: str, errors: list[str]) -> None:
    """Recursively validate *value* against a (subset of) JSON Schema *schema*."""
    type_spec = schema.get("type")
    if type_spec is not None and not _check_type(value, type_spec, location, errors):
        return  # type is wrong; downstream checks would be noise

    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}: must equal const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            errors.append(f"{location}: {value} is less than minimum {minimum}")
        if maximum is not None and value > maximum:
            errors.append(f"{location}: {value} is greater than maximum {maximum}")

    if isinstance(value, dict) and schema.get("type") == "object":
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{location}: missing required property '{key}'")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                child = f"{location}/{key}" if location != "<root>" else key
                _validate_node(value[key], subschema, child, errors)

    if isinstance(value, list) and schema.get("type") == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_node(item, item_schema, f"{location}[{index}]", errors)


def _validate_structural(obj_dict: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _validate_node(obj_dict, schema, "<root>", errors)
    return errors


def validate(obj_dict: dict[str, Any], *, object_type: ObjectType | str | None = None) -> list[str]:
    """Validate a canonical-envelope dict against the schema; return human-readable error strings.

    An empty list means the object is valid. When *object_type* is given, the dict is validated
    against that type's specialized schema (``object_type`` must equal that value); otherwise the
    generic envelope schema is used (``object_type`` must be one of the nine canonical values).

    Uses the ``jsonschema`` library when installed; otherwise a dependency-free structural validator
    covering required keys, types, enum/const membership, and the [0, 1] range for numeric fields.
    """
    schema = _select_schema(object_type)
    if not isinstance(obj_dict, dict):
        return [f"<root>: expected type object, got {type(obj_dict).__name__}"]
    if _has_jsonschema():
        return _validate_with_jsonschema(obj_dict, schema)
    return _validate_structural(obj_dict, schema)


# --------------------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------------------
def export(dir_path: str) -> list[str]:
    """Write each schema in :data:`ALL_SCHEMAS` to ``<dir_path>/<name>.schema.json`` (pretty JSON).

    Creates *dir_path* if needed. Returns the list of written file paths (sorted by name).
    """
    os.makedirs(dir_path, exist_ok=True)
    written: list[str] = []
    for name, schema in ALL_SCHEMAS.items():
        path = os.path.join(dir_path, f"{name}.schema.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(schema, handle, indent=2, sort_keys=False)
            handle.write("\n")
        written.append(path)
    return sorted(written)


__all__ = [
    "ENVELOPE_SCHEMA",
    "ALL_SCHEMAS",
    "object_type_schema",
    "validate",
    "export",
]
