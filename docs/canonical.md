# Canonical cross-layer contracts (Master Spec §8 + §18)

The HydroCognitive Stack Master Spec introduces two cross-cutting contracts the individual layer
PRDs never mandated uniformly. This document is the reconciliation between those contracts and the
layers we already built. Code lives in `hydromemory/canonical/`; rationale in ADR-0047 (envelope),
ADR-0048 (verbs). The unified bus (ADR-0049) and HydroIntegrate (ADR-0050) build on these.

Design principle (ADR-0025): **additive, projection-based**. Existing layer dataclasses are never
mutated. `envelope.py` and `verbs.py` import nothing from the layer packages; only `projection.py`
imports layer schemas.

## §8 — Canonical object envelope

`CanonicalObject` is the minimum shared metadata every object can be projected to, so the bus and
HydroIntegrate can route, gate, and audit any object without layer coupling:

```
id, object_type, source, created_at, owner, confidence, sensitivity,
permissions { visibility, allowed_agents, external_sharing, requires_user_review },
links { derived_from, supports, contradicts, supersedes },
audit { created_by, last_updated, rollback_ref }
```

`ObjectType` is the nine §7 types: observation, memory, identity, intent, judgment, plan, action,
reflection, reintegration. `confidence`/`sensitivity` clamp to [0,1]. `to_dict()` emits exactly the
§8 JSON.

### Projection mapping (per layer)

The envelope is a **projection target**, not a stored field — `to_canonical(obj)` maps each layer
object onto it. The mapping is lossy by design (the §8 subset drops layer-specific richness; the
layer object remains the source of truth). Verified field paths:

| Envelope field | Memory `Droplet` | `Intent` | `JudgmentObject` | `PlanObject` | `ActionObject` | `ReflectionObject` |
|---|---|---|---|---|---|---|
| object_type | MEMORY | INTENT | JUDGMENT | PLAN | ACTION | REFLECTION |
| id | `id` | `id` | `id` | `id` | `id` | `id` |
| created_at | `created_at` | `created_at` | `created_at` | `created_at` | `created_at` | **`observed_at`** |
| source | `source` | `source` | `input.intent_id` | `source.intent_id` | `source_plan_id`/`source_intent_id` | `action_id` |
| owner | `permissions.owner` | `permissions.owner` | "user" (no owner) | "user" | "user" | "user" |
| confidence | `state.confidence` | `governance.confidence` | `scores.truth_confidence` | default | default (not modeled) | `evaluation.success_score` |
| sensitivity | `meta["sensitivity"]`→`state.salinity` | `governance.sensitivity` | `scores.privacy_risk` | default | `risk.privacy_sensitivity` | default |
| permissions | full `Permissions` | full `Permissions` | `JudgmentPermissions` (consent→review) | `meta["requires_user_consent"]` | `AuthorityLevel`→review | defaults |
| links.derived_from | `links.derived_from` | `source_memories` | `input.source_memories`+intent | source intent/judgment | plan/intent/judgment ids | action/plan/intent/judgment ids |
| links.contradicts | `links.contradictions` | `competing_intents` | — | — | — | — |
| audit.created_by | "system" | `source` | "system" | "system" | `actor.id` | "system" |

`ObservationEvent` (HydroSense → OBSERVATION) and `IdentityAnchor` (HydroIdentity → IDENTITY) also
project (ADR-0051/0052): an observation carries no confidence and nothing derives from it; an
identity anchor maps confidence/sensitivity directly, `source_memories` → `derived_from`, and
`meta["supersedes"]` → `supersedes`. With these, `to_canonical` covers all eight built object types.

Notable reconciliations:
- **`contradictions` → `contradicts`** — the memory `Links` field is plural; the envelope is §8-spelled.
- **`observed_at`** — HydroReflect timestamps with `observed_at`, not `created_at`; the projection bridges it.
- **Permissions collapse** — only Memory + Intent carry the full `Permissions`. `JudgmentPermissions.requires_user_consent` → `requires_user_review`; the Action graduated `AuthorityLevel` collapses to `requires_user_review = required ∈ {CONFIRM_REQUIRED, FORBIDDEN}`.
- **`supersedes`** — empty on every current object; populated by HydroIntegrate's SUPERSEDE.

## §18 — Canonical protocol verbs

A declarative registry (`VERB_REGISTRY`) of the 12 layer-neutral verbs → the concrete per-layer
method(s). Aliases, not renames. `resolve_verb(verb, engine)` returns the bound methods present on
the live engine surface (empty when the layer is disabled/unbuilt).

| Verb | Layer | Object | Methods | Implemented |
|---|---|---|---|---|
| SENSE | HydroSense | observation | `sense` | ✓ (ADR-0051) |
| ABSORB | HydroMemory | memory | `absorb` | ✓ |
| RECALL | HydroMemory | memory | `precipitate` | ✓ |
| ANCHOR | HydroIdentity | identity | `anchor` | ✓ (ADR-0052) |
| FORM_INTENT | HydroIntent | intent | `detect_intent`, `propose_intent` | ✓ |
| JUDGE | HydroJudgment | judgment | `evaluate` | ✓ |
| PLAN | HydroPlan | plan | `plan` | ✓ |
| ACT | HydroAction | action | `propose_action`, `execute` | ✓ |
| REFLECT | HydroReflect | reflection | `reflect` | ✓ |
| INTEGRATE | HydroIntegrate | reintegration | `propose_update`, `apply_update` | ✓ (ADR-0050) |
| SUPERSEDE | HydroIntegrate | reintegration | `supersede` | ✓ (ADR-0050) |
| FORGET | HydroMemory | memory | `forget`, `drain` | ✓ |

SUPERSEDE is owned by HydroIntegrate (§18), not Memory — supersession is a governed reintegration
op with audit/rollback, and memory has no `supersede` verb.

## Why this came first

The user chose **canonicalize-then-Integrate**: freeze the shared shapes before building the
loop-closer, so the unified event bus (§17) and HydroIntegrate are built against locked contracts
rather than retrofitted. HydroIntegrate (ADR-0050), then HydroSense (ADR-0051) and HydroIdentity
(ADR-0052) have since landed — **all 12 §18 verbs are now implemented and all nine object types
project**. The 9-layer HydroCognitive Stack is structurally complete.
