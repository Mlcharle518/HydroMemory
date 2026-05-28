# ADR-0047: Canonical cross-layer object envelope (Master Spec §8)

Status: Accepted — implemented (see ../canonical.md)

## Context

The HydroCognitive Stack Master Spec (§7 canonical object model, §8 minimum shared metadata)
introduces a contract the individual layer PRDs never mandated uniformly: **every object, in every
layer, must carry one shared metadata envelope** — `id`, `object_type`, `source`, `created_at`,
`owner`, `confidence`, `sensitivity`, `permissions{visibility/allowed_agents/external_sharing/
requires_user_review}`, `links{derived_from/supports/contradicts/supersedes}`, and
`audit{created_by/last_updated/rollback_ref}`.

We built each layer (Memory, Intent, Judgment, Plan, Action, Reflect) faithful to its own PRD, so
these fields exist but are scattered: ids are uniformly `id`; confidence/sensitivity live in
different nested blocks per layer (`state`, `governance`, `scores`, `evaluation`, `risk`); only
Memory + Intent carry the full `Permissions` type; `audit` and `links.supersedes` are largely
absent. The unified event bus (§17) and HydroIntegrate (the loop-closer) need *one* shape to route,
gate, and audit any object — without importing every layer's schema. The user chose
**canonicalize-then-Integrate**: freeze this envelope first.

## Decision

Add a dependency-light `hydromemory/canonical/` package with the envelope shapes
(`envelope.py`) and project layer objects onto it **additively** — existing layer dataclasses are
never mutated (ADR-0025).

1. **`ObjectType`** — the nine §7 types (observation/memory/identity/intent/judgment/plan/action/
   reflection/reintegration). No layer object stores its own type today; the projection assigns it.
2. **`CanonicalObject`** — the §8 envelope: `id`, `object_type`, `source`, `created_at`, `owner`,
   `confidence`, `sensitivity`, `permissions`, `links`, `audit`. `confidence`/`sensitivity` clamp to
   [0,1]; `to_dict()` emits exactly the §8 JSON. It carries routing/gating/audit metadata only —
   **never the layer-specific body**.
3. **`CanonicalPermissions`** — the §8 four-field subset (visibility/allowed_agents/
   external_sharing/requires_user_review), a projection of the richer memory `Permissions` and the
   narrower `JudgmentPermissions`/`ActionAuthorization`. Visibility is a plain string in
   {private, shared, public} to stay serialization-faithful and import-free.
4. **`CanonicalLinks`** — `derived_from/supports/contradicts/supersedes`. Note the memory `Links`
   field is `contradictions` (plural) → projects to `contradicts`; `supersedes` is new (empty until
   HydroIntegrate populates it).
5. **`CanonicalAudit`** — `created_by/last_updated/rollback_ref`. Mostly absent on current objects;
   defaults to `created_by="system"` and is enriched by HydroIntegrate's rollback machinery.
6. **Dependency rule.** `envelope.py` imports nothing from the layer packages, so it is a stable
   interop contract. The per-layer field mappings live in `projection.py` (ADR — see ../canonical.md),
   the only module that imports layer schemas.

## Consequences

- The bus and HydroIntegrate gain a uniform view of any object (type + owner + permissions +
  confidence/sensitivity + links) without layer coupling — the precondition for §17 routing and
  §15 evidence/consent gating.
- The envelope is a **projection target**, not a new stored field: zero migration, existing tests
  byte-identical, `to_dict()` round-trips for transport/audit.
- Lossy by design: the §8 subset drops layer-specific richness (memory `retention`, judgment
  `blocked_agents`, action graduated `AuthorityLevel`). The layer object remains the source of
  truth; the envelope is the shared-denominator routing view. Per-layer mappings (incl. how
  `AuthorityLevel` collapses to `requires_user_review`) are documented in ../canonical.md.
- Frozen first, on purpose (canonicalize-then-Integrate): projections (ADR-0047 cont.), the unified
  bus (ADR-0049), and HydroIntegrate (ADR-0050) all build against this shape.
