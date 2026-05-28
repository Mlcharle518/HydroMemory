"""Canonical interoperability protocol verbs (Master Spec §18).

A declarative registry mapping each layer-neutral verb (SENSE, ABSORB, RECALL, ANCHOR,
FORM_INTENT, JUDGE, PLAN, ACT, REFLECT, INTEGRATE, SUPERSEDE, FORGET) to the layer that owns it,
the canonical object type it produces/operates on, the :class:`~hydromemory.engine.Engine`
attribute that hosts its surface, and the concrete verb-method name(s) that realize it.

This is the SDK/interop surface: callers name a verb and resolve it to the bound engine methods,
and the registry documents which verbs are *implemented today* vs *pending a future layer*
(SENSE/ANCHOR await HydroSense/HydroIdentity; INTEGRATE/SUPERSEDE await HydroIntegrate). The
verb names map to existing per-layer methods — nothing is renamed (ADR-0048).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from hydromemory.canonical.envelope import ObjectType


class CanonicalVerb(str, Enum):
    SENSE = "SENSE"
    ABSORB = "ABSORB"
    RECALL = "RECALL"
    ANCHOR = "ANCHOR"
    FORM_INTENT = "FORM_INTENT"
    JUDGE = "JUDGE"
    PLAN = "PLAN"
    ACT = "ACT"
    REFLECT = "REFLECT"
    INTEGRATE = "INTEGRATE"
    SUPERSEDE = "SUPERSEDE"
    FORGET = "FORGET"


@dataclass(frozen=True)
class VerbSpec:
    """How a canonical verb maps onto the implementation.

    ``engine_attr`` is the attribute on :class:`~hydromemory.engine.Engine` carrying the layer's
    verb surface (e.g. ``"verbs"`` for memory, ``"intents"`` for HydroIntent), or ``None`` when no
    layer hosts it yet. ``methods`` are the concrete verb-method names on that surface, in
    preference order. ``implemented`` is True when the owning layer exists in this build.
    """

    verb: CanonicalVerb
    layer: str
    object_type: ObjectType | None
    engine_attr: str | None
    methods: tuple[str, ...]
    purpose: str
    implemented: bool


_SPECS: tuple[VerbSpec, ...] = (
    VerbSpec(
        CanonicalVerb.SENSE, "HydroSense", ObjectType.OBSERVATION, "sense", ("sense",),
        "Create an observation event from the current environment.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.ABSORB, "HydroMemory", ObjectType.MEMORY, "verbs", ("absorb",),
        "Create a memory droplet from experience.", implemented=True,
    ),
    VerbSpec(
        CanonicalVerb.RECALL, "HydroMemory", ObjectType.MEMORY, "verbs", ("precipitate",),
        "Surface memory according to phase, context, and permission.", implemented=True,
    ),
    VerbSpec(
        CanonicalVerb.ANCHOR, "HydroIdentity", ObjectType.IDENTITY, "identity", ("anchor",),
        "Create or update a stable identity/value/boundary record.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.FORM_INTENT, "HydroIntent", ObjectType.INTENT, "intents",
        ("detect_intent", "propose_intent"),
        "Create directional intent from memory and identity.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.JUDGE, "HydroJudgment", ObjectType.JUDGMENT, "judgment", ("evaluate",),
        "Evaluate whether and how to proceed.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.PLAN, "HydroPlan", ObjectType.PLAN, "plan", ("plan",),
        "Generate an executable route and contingencies.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.ACT, "HydroAction", ObjectType.ACTION, "action",
        ("propose_action", "execute"),
        "Execute an authorized operation.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.REFLECT, "HydroReflect", ObjectType.REFLECTION, "reflect", ("reflect",),
        "Assess outcome and generate lessons.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.INTEGRATE, "HydroIntegrate", ObjectType.REINTEGRATION, "integrate",
        ("propose_update", "apply_update"),
        "Commit governed learning updates.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.SUPERSEDE, "HydroIntegrate", ObjectType.REINTEGRATION, "integrate",
        ("supersede",),
        "Replace stale objects while preserving history.", implemented=False,
    ),
    VerbSpec(
        CanonicalVerb.FORGET, "HydroMemory", ObjectType.MEMORY, "verbs", ("forget", "drain"),
        "Delete, seal, drain, or compost according to policy.", implemented=True,
    ),
)

VERB_REGISTRY: dict[CanonicalVerb, VerbSpec] = {spec.verb: spec for spec in _SPECS}


def resolve_verb(verb: CanonicalVerb | str, engine: Any) -> list[Any]:
    """Resolve a canonical verb to the bound layer methods available on ``engine``.

    Returns the callables (in the spec's preference order) that actually exist on the engine's
    layer surface, or an empty list when the layer is disabled/absent or unbuilt. This stays
    robust if a method is renamed: only methods present on the live surface are returned.
    """
    key = verb if isinstance(verb, CanonicalVerb) else CanonicalVerb(str(verb))
    spec = VERB_REGISTRY[key]
    if spec.engine_attr is None:
        return []
    surface = getattr(engine, spec.engine_attr, None)
    if surface is None:
        return []
    bound = []
    for name in spec.methods:
        method = getattr(surface, name, None)
        if callable(method):
            bound.append(method)
    return bound


__all__ = [
    "CanonicalVerb",
    "VerbSpec",
    "VERB_REGISTRY",
    "resolve_verb",
]
