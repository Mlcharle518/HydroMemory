# ADR-0036: distilled principles land in CLOUD, not SACRED

Status: Accepted — implemented (refines [ADR-0031](0031-autonomic-consolidation.md))

## Context

The consolidation efficacy eval (`evals/consolidation.py`) surfaced a tension that the
unit tests never could, because they call `distill` in isolation and never *recall* the
result under a non-proxy identity.

`Verbs.distill` ([hydromemory/verbs.py](../../hydromemory/verbs.py)) and
`MeshEngine.distill` ([hydromemory/platform/runtime.py](../../hydromemory/platform/runtime.py))
landed a distilled principle in the **SACRED** reservoir. SACRED is governance-gated:
`check_access` ([hydromemory/governance/enforcement.py](../../hydromemory/governance/enforcement.py))
treats it as `EXPLICIT_USER_CONSENT_OR_USER_DEFINED_CORE_BEHAVIOR` and **denies an ordinary
APPROVED, non-user-proxy agent**. Because recall gates candidates through `permission_score`
(which calls `check_access`), a SACRED principle is filtered out of the candidate set
entirely for such an agent — it never surfaces.

This collides with **ADR-0031's stated goal**: distilled principles are meant to be
*reusable at recall time so agents don't re-derive conclusions*. But the production recall
surface — `Engine.recall` / `Engine.answer` — defaults to
`AgentIdentity(name="assistant", trust_level=APPROVED)`, **not** a user-proxy. So an ordinary
session agent could never recall the very principles consolidation produced for it.

Evidence: `evals/consolidation.py` measured `principle_present_rate` **0.00** under an APPROVED
agent, but **1.00** (principle also ranks #1, compressing 4 episodes → 1) only under a
user-proxy/high-trust identity. The mesh write path was internally consistent — it *writes*
principles as a user-proxy act via `Mesh._principal_vault` — but consistency on the write side
does not help the read side.

The root cause is a **conflation of two different things both called "principle":**

1. A **user-declared** value / vow / identity anchor — what PRD §5.3 actually means by SACRED
   ("User-declared values, vows, principles, identity anchors; not casually overwritten").
   These are correctly consent-gated and overwrite-protected.
2. A **system-distilled** abstraction — autonomic consolidation compressing episodes into
   reusable reasoning (ADR-0031). This is *derived*, not *declared*, and its entire purpose is
   ordinary reuse.

DISTILL routed (2) into (1)'s reservoir.

## Decision

**Distilled principles land in the `CLOUD` reservoir** (both `Verbs.distill` and
`MeshEngine.distill`), keeping `phase=GROUNDWATER`. SACRED is reserved for genuinely
user-declared identity memory.

Why CLOUD specifically:

- **It is the abstraction layer.** CLOUD is described (`reservoirs.py`) as "Abstracted pattern
  clusters … useful for distillation," and `evaporate`→VAPOR/CLOUD and `condense`→CLOUD already
  land their outputs there. A distilled principle is the top of that same
  `evaporate→condense→distill` ladder, so CLOUD makes the ladder consistent.
- **It is approved-agent readable.** CLOUD's access level is `approved_agents`, so the default
  `Engine.answer` / `Engine.recall` identity (APPROVED `assistant`) can recall and reuse it —
  which is exactly ADR-0031's goal.
- **`phase=GROUNDWATER` is retained** so the principle still reads as a *settled* abstraction
  and keeps the `abstraction_bonus` (which is **phase-keyed**, not reservoir-keyed:
  `recall._ABSTRACTION_PHASES`), letting it outrank its literal sources. The recall threshold
  for `(GROUNDWATER, CLOUD)` is **0.60** vs `(GROUNDWATER, SACRED)`'s 0.70 — slightly *easier*
  to surface, never harder.

**Rejected alternatives:**

- **Keep SACRED, document that reuse needs a user-proxy/high-trust recall identity** (option 1).
  The only way to make the *default* recall path reuse principles would be to elevate the
  default assistant identity to user-proxy — which would widen access to **all** SACRED memory
  (vows, values, private anchors), a serious governance regression. Documenting the limitation
  without changing it leaves ADR-0031's goal unmet for ordinary agents.
- **GROUNDWATER as the target reservoir** (the exploratory "e.g. GROUNDWATER" in the framing).
  GROUNDWATER is `high_trust_agents_only`, so an APPROVED agent is **still denied** — it does
  not fix the finding. CLOUD is the reservoir an approved agent can actually read.
- **A read carve-out / capability grant for SACRED principles** (option 3). A freely
  approved-readable SACRED droplet contradicts SACRED's defining property (restricted access);
  it would effectively reinvent "CLOUD with overwrite-protection" inside SACRED and add a new
  special case to the enforcement gate. Moving to the semantically correct reservoir is the
  more honest fix. (ADR-0031 itself notes principle dedup/superseding as future work — i.e.
  principles *should* be updatable — so SACRED's `overwrite_allowed=false` was a poor fit
  anyway.)

## Consequences

- **The reuse goal is met for ordinary agents.** `evals/consolidation.py` now scores under a
  plain APPROVED `assistant` (mirroring `Engine.answer`'s default) and reports
  `principle_present_rate` 1.00 / `principle_top1_rate` 1.00 — the benefit materializes without
  any privileged identity.
- **SACRED's integrity is preserved.** Its genuine population path is untouched: the capture
  pipeline still routes high-sensitivity, user-declared memory to SACRED
  (`route_to_reservoir`), and that memory stays consent-gated and overwrite-protected.
- **The mesh `_principal_vault` (user-proxy) write path is retained but no longer *required*.**
  Writing consolidated memory as the owner/principal is still sensible, but a CLOUD write
  clears governance for the mesh's working identity too. `Mesh._is_principle` now keys on
  `source=="distill"` / the `principle` meta marker (with SACRED kept as a back-compat signal),
  so the never-re-consolidate guard is unaffected by the reservoir change.
- **Additive / default-off preserved (ADR-0025).** Nothing distills unless a caller invokes
  `distill` or enables `Mesh(consolidate=True)`; the default path is unchanged. The reservoir
  change is a behavior change to the `distill` *output*, covered by updating the four tests
  that pinned `reservoir is SACRED` (`test_b_verbs`, `test_consolidation` ×2,
  `test_mesh_consolidation`) to `CLOUD`; the full suite stays green.
- **A deliberate "enshrine as a SACRED anchor" affordance is intentionally not built here.** If
  a future need arises for a caller to distill *into* SACRED on purpose, add an opt-in
  `reservoir=` parameter to `distill` (default CLOUD) rather than reverting this default. Noted,
  not built (no speculative surface).
