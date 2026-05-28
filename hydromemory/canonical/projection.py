"""Per-layer projection onto the canonical §8 envelope (ADR-0047).

This is the *only* canonical module that imports the layer schemas, and it imports the upper
layers **optionally** so it can ship in the open core (where only the memory ``Droplet`` is
present). :func:`to_canonical` dispatches by type over the memory droplet plus each installed
upper-layer object (intent, judgment, plan, action, reflection, observation, identity) and maps it
onto :class:`~hydromemory.canonical.envelope.CanonicalObject`, the minimum-shared-metadata shape
(Master Spec §8). The unified event bus (§17) and HydroIntegrate (the loop-closer) consume the
projection so they can route, gate, and audit any object uniformly.

Projection is strictly additive: it *reads* the existing layer dataclasses and never mutates them
(ADR-0025, ADR-0047). The envelope carries only routing/gating/audit metadata, never the layer
body. Each helper reads optionals defensively (``getattr`` with defaults, coercing enums via
``.value``) and lets the envelope apply its own defaults for fields a layer does not model
(e.g. confidence on actions, sensitivity on plans).
"""
from __future__ import annotations

import importlib
from typing import Any

from hydromemory.canonical.envelope import (
    CanonicalAudit,
    CanonicalLinks,
    CanonicalObject,
    CanonicalPermissions,
    ObjectType,
)
from hydromemory.schema import Droplet


def _opt(module: str, name: str) -> Any:
    """Load an optional upper-layer type, or ``None`` when that layer isn't installed.

    In the open-core ("HydroMemory") distribution the eight upper cognitive-layer subpackages are
    absent; the memory ``Droplet`` is always present. Keeping these imports optional lets the
    canonical layer ship in the open core and still project every object in the full HydroCognitive
    build (ADR-0047/0051/0052).
    """
    try:
        return getattr(importlib.import_module(module), name)
    except ImportError:  # pragma: no cover - open-core build (upper layers absent)
        return None


# Upper-layer object types — present in the full stack, absent in the open core.
Intent = _opt("hydromemory.hydrointent.schema", "Intent")
JudgmentObject = _opt("hydromemory.hydrojudgment.schema", "JudgmentObject")
PlanObject = _opt("hydromemory.hydroplan.schema", "PlanObject")
ActionObject = _opt("hydromemory.hydroaction.schema", "ActionObject")
AuthorityLevel = _opt("hydromemory.hydroaction.schema", "AuthorityLevel")
ReflectionObject = _opt("hydromemory.hydroreflect.schema", "ReflectionObject")
ObservationEvent = _opt("hydromemory.hydrosense.schema", "ObservationEvent")
IdentityAnchor = _opt("hydromemory.hydroidentity.schema", "IdentityAnchor")


def _compact(ids: list[str | None]) -> list[str]:
    """Drop empty/None link ids while preserving order."""
    return [i for i in ids if i]


def _droplet_to_canonical(droplet: Droplet) -> CanonicalObject:
    perms = droplet.permissions
    # Sensitivity: an explicit meta override wins, else the salinity state float (§5.2).
    sensitivity = droplet.meta.get("sensitivity")
    if sensitivity is None:
        sensitivity = droplet.state.salinity
    return CanonicalObject(
        id=droplet.id,
        object_type=ObjectType.MEMORY,
        source=droplet.source,
        created_at=droplet.created_at,
        owner=perms.owner,
        confidence=droplet.state.confidence,
        sensitivity=sensitivity,
        permissions=CanonicalPermissions(
            visibility=perms.visibility.value,
            allowed_agents=list(perms.allowed_agents),
            external_sharing=perms.external_sharing,
            requires_user_review=perms.requires_user_review,
        ),
        links=CanonicalLinks(
            derived_from=list(droplet.links.derived_from),
            supports=list(droplet.links.supports),
            contradicts=list(droplet.links.contradictions),
            supersedes=[],
        ),
        audit=CanonicalAudit(created_by="system", rollback_ref=None),
    )


def _intent_to_canonical(intent: Any) -> CanonicalObject:
    perms = intent.permissions
    return CanonicalObject(
        id=intent.id,
        object_type=ObjectType.INTENT,
        source=intent.source,
        created_at=intent.created_at,
        owner=perms.owner,
        confidence=intent.governance.confidence,
        sensitivity=intent.governance.sensitivity,
        permissions=CanonicalPermissions(
            visibility=perms.visibility.value,
            allowed_agents=list(perms.allowed_agents),
            external_sharing=perms.external_sharing,
            requires_user_review=perms.requires_user_review,
        ),
        links=CanonicalLinks(
            derived_from=list(intent.source_memories),
            supports=[],
            contradicts=list(intent.competing_intents),
            supersedes=[],
        ),
        audit=CanonicalAudit(created_by=intent.source),
    )


def _judgment_to_canonical(judgment: Any) -> CanonicalObject:
    perms = judgment.permissions
    intent_id = judgment.input.intent_id
    derived_from = _compact([*judgment.input.source_memories, intent_id])
    return CanonicalObject(
        id=judgment.id,
        object_type=ObjectType.JUDGMENT,
        source=intent_id or "",
        created_at=judgment.created_at,
        owner="user",
        confidence=judgment.scores.truth_confidence,
        sensitivity=judgment.scores.privacy_risk,
        permissions=CanonicalPermissions(
            visibility="private",
            allowed_agents=list(perms.allowed_agents),
            requires_user_review=perms.requires_user_consent,
        ),
        links=CanonicalLinks(derived_from=derived_from),
        audit=CanonicalAudit(created_by="system"),
    )


def _plan_to_canonical(plan: Any) -> CanonicalObject:
    requires_review = bool(plan.meta.get("requires_user_consent", False))
    derived_from = _compact([plan.source.intent_id, plan.source.judgment_id])
    return CanonicalObject(
        id=plan.id,
        object_type=ObjectType.PLAN,
        source=plan.source.intent_id or "",
        created_at=plan.created_at,
        owner="user",
        permissions=CanonicalPermissions(requires_user_review=requires_review),
        links=CanonicalLinks(derived_from=derived_from),
        audit=CanonicalAudit(created_by="system"),
    )


def _action_to_canonical(action: Any) -> CanonicalObject:
    # Only reached when the action layer is installed, so AuthorityLevel is non-None here.
    requires_review = action.authorization.required in {
        AuthorityLevel.CONFIRM_REQUIRED,
        AuthorityLevel.FORBIDDEN,
    }
    derived_from = _compact([action.source_plan_id, action.source_intent_id, action.judgment_id])
    return CanonicalObject(
        id=action.id,
        object_type=ObjectType.ACTION,
        source=action.source_plan_id or action.source_intent_id or "",
        created_at=action.created_at,
        owner="user",
        sensitivity=action.risk.privacy_sensitivity,
        permissions=CanonicalPermissions(requires_user_review=requires_review),
        links=CanonicalLinks(derived_from=derived_from),
        audit=CanonicalAudit(created_by=action.actor.id),
    )


def _reflection_to_canonical(reflection: Any) -> CanonicalObject:
    derived_from = _compact(
        [reflection.action_id, reflection.plan_id, reflection.intent_id, reflection.judgment_id]
    )
    return CanonicalObject(
        id=reflection.id,
        object_type=ObjectType.REFLECTION,
        source=reflection.action_id or "",
        created_at=reflection.observed_at,
        owner="user",
        confidence=reflection.evaluation.success_score,
        links=CanonicalLinks(derived_from=derived_from),
        audit=CanonicalAudit(created_by="system"),
    )


def _observation_to_canonical(event: Any) -> CanonicalObject:
    # An observation is raw environment input: it models no confidence, and nothing is "derived
    # from" it (the droplet it later produces points back at it, not the reverse). Sensitivity may
    # be tagged in meta.
    return CanonicalObject(
        id=event.id,
        object_type=ObjectType.OBSERVATION,
        source=event.source,
        created_at=event.observed_at,
        owner="user",
        sensitivity=float(event.meta.get("sensitivity", 0.0) or 0.0),
        audit=CanonicalAudit(created_by="hydrosense"),
    )


def _identity_to_canonical(anchor: Any) -> CanonicalObject:
    perms = anchor.permissions
    return CanonicalObject(
        id=anchor.id,
        object_type=ObjectType.IDENTITY,
        source=anchor.source,
        created_at=anchor.created_at,
        owner=perms.owner,
        confidence=anchor.confidence,
        sensitivity=anchor.sensitivity,
        permissions=CanonicalPermissions(
            visibility=perms.visibility.value,
            allowed_agents=list(perms.allowed_agents),
            external_sharing=perms.external_sharing,
            requires_user_review=perms.requires_user_review,
        ),
        links=CanonicalLinks(
            derived_from=list(anchor.source_memories),
            supersedes=list(anchor.meta.get("supersedes", []) or []),
        ),
        audit=CanonicalAudit(created_by=anchor.source),
    )


def to_canonical(obj: Any) -> CanonicalObject:
    """Project a built layer object onto the canonical §8 envelope.

    Dispatches by type over the memory ``Droplet`` (always present) and the upper-layer objects
    (Intent, JudgmentObject, PlanObject, ActionObject, ReflectionObject, ObservationEvent,
    IdentityAnchor) **when their layer is installed** — in the open-core build those are absent and
    simply don't match. Raises :class:`TypeError` for anything unmapped so callers do not silently
    route an unknown object onto the bus.
    """
    if isinstance(obj, Droplet):
        return _droplet_to_canonical(obj)
    if Intent is not None and isinstance(obj, Intent):
        return _intent_to_canonical(obj)
    if JudgmentObject is not None and isinstance(obj, JudgmentObject):
        return _judgment_to_canonical(obj)
    if PlanObject is not None and isinstance(obj, PlanObject):
        return _plan_to_canonical(obj)
    if ActionObject is not None and isinstance(obj, ActionObject):
        return _action_to_canonical(obj)
    if ReflectionObject is not None and isinstance(obj, ReflectionObject):
        return _reflection_to_canonical(obj)
    if ObservationEvent is not None and isinstance(obj, ObservationEvent):
        return _observation_to_canonical(obj)
    if IdentityAnchor is not None and isinstance(obj, IdentityAnchor):
        return _identity_to_canonical(obj)
    raise TypeError(f"to_canonical: no projection for {type(obj).__name__}")


__all__ = ["to_canonical"]
