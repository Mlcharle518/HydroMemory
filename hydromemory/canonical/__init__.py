"""Canonical cross-layer contracts for the HydroCognitive Stack.

The Master Spec (§8 minimum shared metadata, §18 interoperability verbs) introduces two
cross-cutting contracts that the individual layer PRDs did not mandate uniformly:

* the **canonical object envelope** — one shared metadata shape every layer object can be
  projected to, so the unified event bus and HydroIntegrate can route/gate/audit objects
  without importing each layer's schema (`envelope`);
* the **canonical protocol verb set** — a layer-neutral 12-verb interop surface mapped to the
  concrete per-layer verb methods (`verbs`).

These modules are deliberately dependency-light: the envelope/verb shapes import nothing from
the layer packages. The per-layer field mappings live in `projection`, which is the only place
that imports the layer schemas — keeping existing layer dataclasses untouched (additive,
ADR-0025).
"""
from __future__ import annotations

from hydromemory.canonical.envelope import (
    VISIBILITIES,
    CanonicalAudit,
    CanonicalLinks,
    CanonicalObject,
    CanonicalPermissions,
    ObjectType,
)
from hydromemory.canonical.jsonschema import (
    ALL_SCHEMAS,
    ENVELOPE_SCHEMA,
    export,
    object_type_schema,
    validate,
)
from hydromemory.canonical.projection import to_canonical
from hydromemory.canonical.verbs import (
    VERB_REGISTRY,
    CanonicalVerb,
    VerbSpec,
    resolve_verb,
)

__all__ = [
    "VISIBILITIES",
    "CanonicalAudit",
    "CanonicalLinks",
    "CanonicalObject",
    "CanonicalPermissions",
    "ObjectType",
    "to_canonical",
    "ENVELOPE_SCHEMA",
    "ALL_SCHEMAS",
    "object_type_schema",
    "validate",
    "export",
    "VERB_REGISTRY",
    "CanonicalVerb",
    "VerbSpec",
    "resolve_verb",
]
