"""Canonical §8 object envelope (ADR-0047)."""
from __future__ import annotations

from datetime import UTC, datetime

from hydromemory.canonical.envelope import (
    VISIBILITIES,
    CanonicalAudit,
    CanonicalLinks,
    CanonicalObject,
    CanonicalPermissions,
    ObjectType,
)


def test_object_type_covers_the_nine_layers():
    assert {t.value for t in ObjectType} == {
        "observation", "memory", "identity", "intent",
        "judgment", "plan", "action", "reflection", "reintegration",
    }


def test_envelope_to_dict_emits_exact_section8_shape():
    obj = CanonicalObject(id="mem_1", object_type=ObjectType.MEMORY)
    d = obj.to_dict()
    assert set(d) == {
        "id", "object_type", "source", "created_at", "owner",
        "confidence", "sensitivity", "permissions", "links", "audit",
    }
    assert set(d["permissions"]) == {"visibility", "allowed_agents", "external_sharing", "requires_user_review"}
    assert set(d["links"]) == {"derived_from", "supports", "contradicts", "supersedes"}
    assert set(d["audit"]) == {"created_by", "last_updated", "rollback_ref"}


def test_envelope_round_trips():
    created = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    obj = CanonicalObject(
        id="int_7",
        object_type=ObjectType.INTENT,
        source="detect",
        created_at=created,
        owner="user",
        confidence=0.8,
        sensitivity=0.3,
        permissions=CanonicalPermissions(visibility="shared", allowed_agents=["assistant"], requires_user_review=True),
        links=CanonicalLinks(derived_from=["mem_1", "mem_2"], contradicts=["int_3"]),
        audit=CanonicalAudit(created_by="assistant", last_updated=created, rollback_ref="snap_1"),
    )
    back = CanonicalObject.from_dict(obj.to_dict())
    assert back.to_dict() == obj.to_dict()
    assert back.object_type is ObjectType.INTENT
    assert back.created_at == created
    assert back.links.derived_from == ["mem_1", "mem_2"]
    assert back.audit.rollback_ref == "snap_1"


def test_confidence_and_sensitivity_clamp_to_unit():
    obj = CanonicalObject(id="x", object_type=ObjectType.ACTION, confidence=5.0, sensitivity=-2.0)
    assert obj.confidence == 1.0
    assert obj.sensitivity == 0.0


def test_object_type_coerced_from_string():
    obj = CanonicalObject(id="x", object_type="judgment")  # type: ignore[arg-type]
    assert obj.object_type is ObjectType.JUDGMENT


def test_invalid_visibility_falls_back_to_private():
    perms = CanonicalPermissions(visibility="top-secret")
    assert perms.visibility == "private"
    assert "private" in VISIBILITIES


def test_audit_handles_missing_optional_fields():
    audit = CanonicalAudit.from_dict({})
    assert audit.created_by == "system"
    assert audit.last_updated is None
    assert audit.rollback_ref is None
