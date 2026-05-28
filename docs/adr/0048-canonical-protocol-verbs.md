# ADR-0048: Canonical interoperability protocol verbs (Master Spec §18)

Status: Accepted — implemented (see ../canonical.md)

## Context

Master Spec §18 names a layer-neutral 12-verb interoperability surface — SENSE, ABSORB, RECALL,
ANCHOR, FORM_INTENT, JUDGE, PLAN, ACT, REFLECT, INTEGRATE, SUPERSEDE, FORGET — intended as the
protocol/SDK contract across the stack. Our layers already implement these capabilities, but under
per-layer method names (e.g. FORM_INTENT → `detect_intent`/`propose_intent`; JUDGE → `evaluate`;
ACT → `propose_action`/`execute`; RECALL → `precipitate`; FORGET → `forget`/`drain`). Without a
canonical mapping, an SDK consumer or the unified bus cannot address the stack uniformly.

## Decision

Add a **declarative verb registry** (`hydromemory/canonical/verbs.py`) — aliases, not renames. No
existing method changes.

1. **`CanonicalVerb`** — the 12 §18 verbs as an enum.
2. **`VerbSpec`** — per verb: owning `layer`, canonical `object_type`, the `Engine` attribute that
   hosts the surface (`engine_attr`: `"verbs"`, `"intents"`, `"judgment"`, `"plan"`, `"action"`,
   `"reflect"`, `"integrate"`, or `None`), the concrete `methods` in preference order, a `purpose`
   string, and an `implemented` flag.
3. **`VERB_REGISTRY`** — the frozen mapping. Verified against the live verb surfaces:
   ABSORB→`absorb`, RECALL→`precipitate`, FORGET→`forget`/`drain` (memory); FORM_INTENT→
   `detect_intent`/`propose_intent`; JUDGE→`evaluate`; PLAN→`plan`; ACT→`propose_action`/`execute`;
   REFLECT→`reflect`. SENSE (HydroSense) and ANCHOR (HydroIdentity) are `implemented=False` —
   pending those layers. INTEGRATE and SUPERSEDE are `implemented=False` until HydroIntegrate
   (ADR-0050) lands, at which point their flags flip and `methods` resolve.
4. **`resolve_verb(verb, engine)`** — returns the bound methods that actually exist on the engine's
   layer surface (empty when the layer is disabled/absent). Robust to renames: only live methods are
   returned, so a fully-enabled engine can be asserted to resolve every `implemented` verb.

## Consequences

- A layer-neutral surface exists for the SDK and the unified bus to dispatch by verb, decoupled
  from per-layer method names.
- The registry is the single source of truth for "which canonical capabilities exist in this build."
  As HydroSense/HydroIdentity/HydroIntegrate land, their specs flip to `implemented=True`.
- SUPERSEDE is deliberately owned by HydroIntegrate (§18), not Memory — memory has no `supersede`
  verb; supersession is a governed reintegration operation with audit/rollback.
- Aliases-not-renames keeps every existing test and caller working; the canonical names are an
  additive interop layer.
