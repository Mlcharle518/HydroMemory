# Architecture Decision Records

These ADRs record the decisions that reconcile the HydroMemory PRD (the source
spec) with this reference implementation — places where the spec was
inconsistent, under-specified, or silent, and a concrete choice had to be made.
Each record follows the same shape: **Status**, **Context**, **Decision**,
**Consequences**.

ADRs **0030+** are *gap-closure* decisions from [closing-the-gaps.md](../closing-the-gaps.md)
(HydroMemory vs. the four hard problems of LLM memory), not spec reconciliations. 0030 is
implemented (Phase 1, the spreading-activation spine); 0031–0034 are accepted directions
whose implementation is phased (see closing-the-gaps.md §5).

| ADR | Decision |
| --- | -------- |
| [0001](0001-id-canonical-and-spec-aliases.md) | `id` is canonical; spec aliases (`memory_id`, `charge`, `scope`, `agent_access`) accepted on ingest. |
| [0002](0002-type-and-semantic-tags-first-class.md) | `type` and `semantic_tags` promoted to first-class fields (though §7 omits them). |
| [0003](0003-phase-enum-and-storable-subset.md) | One `Phase` enum of 13; `STORABLE_PHASES` = the §7 nine (river/snow/fog/steam are transient-only). |
| [0004](0004-permissions-superset.md) | `Permissions` = §7 set + `requires_consent_for_external_use` + `requires_user_review`. |
| [0005](0005-reservoir-canonical-ids-and-aliases.md) | Reservoir canonical short ids + an alias map for the spec's display names. |
| [0006](0006-recall-weights-default-and-clamp.md) | Recall terms unweighted by default (`RecallWeights` all 1.0); each term clamped to `[0,1]`. |
| [0007](0007-recall-threshold-table.md) | `recall_threshold` = phase-base + reservoir-additive table (spec left it unspecified). |
| [0008](0008-synthetic-transition-triggers.md) | Synthetic transition conditions (similarity/association/repetition/density/...) modeled as engine-emitted triggers. |
| [0009](0009-missing-rule-flag-defaults.md) | §10 policy flags and §5.4 thresholds get documented defaults. |
| [0010](0010-verb-co-ownership.md) | Co-owned verbs delegate to governance / forgetting / contamination. |
| [0011](0011-stub-first-pluggable-intelligence.md) | Stub-first pluggable intelligence; Claude backend lazy-imported. |
| [0012](0012-sqlite-plus-vector-index-storage.md) | Storage = SQLite + a file-backed brute-force cosine vector index. |
| [0013](0013-typescript-client-over-http.md) | TypeScript client over an HTTP/JSON boundary (not in-process bindings). |
| [0014](0014-defer-os-bus-and-mesh.md) | Defer the §9 OS memory bus + live agent mesh; leave the seams open. |
| [0015](0015-governance-obligations-returned.md) | Governance obligations are returned, not auto-applied. |
| [0016](0016-in-process-asyncio-bus-with-ws-bridge.md) | §9 bus is in-process pub/sub + a FastAPI WebSocket bridge (no external broker). |
| [0017](0017-sync-core-publish-async-drain-noop-emit-seam.md) | Bus `publish` is sync-core; WS subscribers drain a bounded drop-oldest queue; emit defaults to `NULL_EMITTER`. |
| [0018](0018-permission-gated-event-delivery.md) | Event delivery is permission-gated via context-free READ `check_access` (glacier events reach no subscriber). |
| [0019](0019-vault-encrypt-which-fields.md) | Vault encrypts content/tags/state/cycle/meta to one token; routing columns stay plaintext; pluggable Fernet/Null cipher. |
| [0020](0020-vector-index-decrypted-in-process-cache.md) | The vector index is a decrypted-in-process cache; embeddings stored plaintext (documented leak). |
| [0021](0021-append-only-hash-chained-audit-log.md) | Append-only, hash-chained audit log (`verify_chain`); cross-app scope short-circuit is unaudited. |
| [0022](0022-l1-app-scoping-l2-cross-app-shared-backing.md) | L1 app scoping via an `app_id` column; L1/L2 views share ONE backing repo to avoid stale indexes. |
| [0023](0023-grants-narrow-never-widen.md) | Capability grants only narrow (allow→deny); user-proxy owner bypasses the grant layer (unaudited). |
| [0024](0024-mesh-agents-as-subscribers-cascade-safety.md) | L3 mesh = §8 agents as bus subscribers with cascade safety; `tick` untouched. |
| [0025](0025-additive-layering-v1-stays-green.md) | v2 is new modules + no-op-default seams; v1 stayed green (276 → 386 tests). |
| [0026](0026-real-model-backends.md) | Composable embedder/text-ops factory; local sentence-transformers embeddings; `abstraction_bonus` recall lever (default 0.0); hardened Claude backend (structured output, opus-4-7 default). |
| [0027](0027-vault-key-rotation.md) | Vault key rotation: `MultiFernet` primary + retired keys, token-level `rotate`, owner-gated vault-wide `rotate_keys` re-encryption migration (audited, idempotent). |
| [0028](0028-audit-out-of-scope-access-attempts.md) | Audit out-of-scope cross-app access attempts on the single-id methods (closes the ADR-0021 gap); bulk query/search scope-filtering stays silent. |
| [0029](0029-keyless-to-encrypted-vault-migration.md) | Keyless → encrypted first-time vault migration: detect plaintext (JSON-object) tokens and Fernet-encrypt in place (`encrypt_plaintext_rows`/`encrypt_vault`); owner-only, idempotent. |
| [0030](0030-query-conditioned-spreading-activation.md) | Query-conditioned spreading activation over `links` (fluidity=conductance, depth=resistance, purity=mixing); opt-in `activation_bonus` (default 0.0). Closes multi-hop recall + yields the `cluster` primitive. **(Phase 1 — built.)** |
| [0031](0031-autonomic-consolidation.md) | Autonomic consolidation: implement `cluster` (from 0030) + a `cluster`→`condense`/`distill` cadence on mesh/`DENSITY`/`SIMILARITY`, bounded by ADR-0024; principles land in `SACRED`. *(Phase 2 — deferred.)* |
| [0032](0032-time-decay-autonomic-forgetting.md) | Per-cycle decay of *salience only* (`pressure`/`fluidity`/`temperature`; never `purity`/`integrity`/`confidence`) + a real `aged_droplets` query; demote-not-delete. *(Phase 2 — deferred.)* |
| [0033](0033-context-assembly-working-set-packing.md) | Working-set packer over recall output: token budget + primacy/recency placement + abstraction preference + provenance dedup; default passthrough. *(Phase 3 — deferred.)* |
| [0034](0034-retrieval-scale-ann.md) | Pluggable ANN backend behind the unchanged `VectorIndex`/`search_similar` contract (optional heavy extra); brute-force stays the exact default; recall@k parity test. *(Phase 3 — deferred.)* |
| [0035](0035-reader-over-activated-constellation.md) | Reader: compose an answer over the recalled constellation with `[n]`→droplet-id citations; pluggable composer (offline extractive default / Claude); `Engine.answer` (traverse on by default). Finishes the research-note composition step. **(Built.)** |
| [0036](0036-distilled-principles-land-in-cloud.md) | Distilled principles land in **CLOUD** (approved-agent readable, the abstraction layer), not SACRED — so ordinary approved agents can reuse them at recall, meeting ADR-0031's goal; SACRED reserved for user-declared anchors. Refines ADR-0031. **(Built.)** |
| [0037](0037-intent-object-model-and-reconciliation.md) | **HydroIntent** layer (PDF Phase 4): `Intent` object model + lifecycle as an additive, default-off `hydromemory/hydrointent/` subpackage reusing `Permissions`/`Cycle`/storage/bus. Reconciles a top-down architecture doc against the over-produced impl (reuse-over-rebuild). See [../hydrointent.md](../hydrointent.md). **(Built — Phase A.)** |
| [0038](0038-stricter-intent-governance.md) | Stricter intent governance: `intent_access` reuses the governance value types + adds a higher read floor, a user-review gate, an over-inference gate (support + confidence), and anti-manipulation. Memory governance untouched. **(Built.)** |
| [0039](0039-memory-to-intent-distillation.md) | Memory→intent distillation reuses `activation.cluster` + the abstractor (the ADR-0031/0036 machinery); explainability falls out of `source_memories`. PDF §13.1 is the golden test. **(Built.)** |
| [0040](0040-intent-agents-and-runtime.md) | HydroIntent **Phase B**: completes the §9 verb set (conflict detection/resolution, Judgment/Plan handoffs, drain) + the 7 intent agent roles + `build_hydrointent_runtime`, reusing the memory `AgentRuntime` verbatim. **(Built.)** |
| [0041](0041-autonomic-intent-detection.md) | Autonomic intent detection: a standalone bus reaction (`AutonomicIntentDetector`) that proposes a CANDIDATE intent from a dense memory constellation on a memory event — the `Mesh(consolidate=True)` analog, cascade-bounded, review-gated. **(Built.)** |
| [0042](0042-align-to-full-hydrointent-prd.md) | Align HydroIntent to the **full PRD v1.0**: richer Intent schema (`DirectionVector` + `IntentGovernance` + desired/current state), PRD lifecycle states, full §16 verb set (merge/split/defer/query/retire), §12 `intent_force`, first-class `Conflict`, §14 agent roles. Supersedes the schema/lifecycle/verb/agent specifics of ADR-0037/0040. **(Built.)** |
| [0043](0043-hydrojudgment-layer.md) | **HydroJudgment** (stack position 5): the discernment layer. Judgment Object + 7 decision classes + multi-axis evaluation/classifier (safety-first) + §11 verbs (evaluate/weigh/gate/verify/constrain/redirect/escalate/explain) + §12 agents + audit log. Additive/default-off; consumes Intent objects, routes `proceed*` to HydroPlan. See [../hydrojudgment.md](../hydrojudgment.md). **(Built.)** |
| [0044](0044-hydroplan-layer.md) | **HydroPlan** (stack position 6): the strategy/sequencing layer. Plan Object + milestones/dependencies + plan taxonomy + topological sequencing + §9 plan-quality + §10 verbs (plan/decompose/sequence/assign/schedule/checkpoint/simulate/replan/handoff) + §11 agents. Consumes Intent + Judgment; imports constraints + refuses to plan a non-proceed judgment (HP-003). See [../hydroplan.md](../hydroplan.md). **(Built — Phase C complete.)** |
| [0045](0045-hydroaction-layer.md) | **HydroAction** (stack position 7): the execution layer. Action Object + lifecycle + authority/preflight model + §14 verbs (propose/preflight/authorize/simulate/execute/observe/verify/pause/rollback/escalate/log) + §15 agents + audit trail. Pluggable, safe-by-default executor (no real side effects unless injected); refuses to execute a non-approved judgment (§17). See [../hydroaction.md](../hydroaction.md). **(Built.)** |
| [0046](0046-hydroreflect-layer.md) | **HydroReflect** (stack position 8): the outcome-interpretation/learning layer. Reflection Object + §10 outcome taxonomy + §11 four-frame comparison model + §13 verbs (reflect/observe/compare/classify/interpret/diagnose/learn/correct/escalate/recommend/close_loop) + §14 agents. Consumes Action outcomes → lessons + recommended_updates for HydroIntegrate; tentative low-evidence reflections withhold identity updates (§16). Closes the cognitive loop. See [../hydroreflect.md](../hydroreflect.md). **(Built.)** |
