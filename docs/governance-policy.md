# Governance & Access Policy

Safety, access, and contamination for the HydroMemory Protocol reference
implementation (PRD §10, §10.1). This document describes what
`hydromemory/governance/` and `hydromemory/contamination.py` actually implement.
Where the implementation extends the PRD, **the code is the source of truth** and
the difference is called out.

HydroMemory "must never treat all memory equally" (§10): reservoir, phase,
sensitivity, and confidence determine access. Governance is the logical **AND** of
two layers — the per-reservoir rule and the droplet's own permissions — surfaced
through a single entry point, `check_access`.

- [Reservoir access policy](#reservoir-access-policy)
- [The `check_access` contract](#the-check_access-contract)
- [Trust gating and the agent allowlist](#trust-gating-and-the-agent-allowlist)
- [Scoring: permission_score and privacy_risk](#scoring-permission_score-and-privacy_risk)
- [Contamination](#contamination)

---

## Reservoir access policy

The policy is **data, not code**. `hydromemory/governance/policy_data.json`
reproduces the §10 `reservoir_access` block, and `policy.py` loads it into frozen
`ReservoirRule` dataclasses keyed by `Reservoir`. Each rule has an `access_level`
plus five booleans.

The table below is **verbatim from `policy_data.json`**. Any flag a rule omits
inherits the documented defaults (`user_visible=False`,
`requires_explanation=False`, `requires_thaw_protocol=False`,
`usable_for_response=True`, `overwrite_allowed=True`).

| Reservoir | `access_level` | `user_visible` | `requires_explanation` | `requires_thaw_protocol` | `usable_for_response` | `overwrite_allowed` |
| --- | --- | :---: | :---: | :---: | :---: | :---: |
| `working_stream` | `session_agents` | false | false | false | true | true |
| `surface` | `approved_agents` | **true** | false | false | true | true |
| `groundwater` | `high_trust_agents_only` | false | **true** | false | true | true |
| `glacier` | `explicit_user_consent` | false | false | **true** | true | true |
| `contaminated` | `filtration_agent_only` | false | false | false | **false** | true |
| `sacred` | `explicit_user_consent_or_user_defined_core_behavior` | false | false | false | true | **false** |
| `cloud` | `approved_agents` | false | false | false | true | true |
| `ocean` | `high_trust_agents_only` | false | false | false | true | true |

Bold cells are the values a rule sets explicitly; the rest are inherited defaults.

> **`cloud` and `ocean` are not in the PRD §10 block.** §10 enumerates only six
> reservoirs (working_stream, surface, groundwater, glacier, contaminated,
> sacred). The implementation adds `cloud` and `ocean` with documented defaults so
> all eight reservoirs in §5.3 have a rule:
> - `cloud` behaves like an abstracted `surface` layer → `approved_agents`.
> - `ocean` is the collective, privacy-bounded layer → `high_trust_agents_only`.
> These are the source of truth for those two reservoirs.

### `AccessLevel`

The `access` strings from §10, normalized to the `AccessLevel` enum
(`policy.py`):

| `AccessLevel` | Meaning |
| --- | --- |
| `SESSION_AGENTS` | Any session-scoped agent may access. |
| `APPROVED_AGENTS` | Approved agents (trust ≥ approved). |
| `HIGH_TRUST_AGENTS_ONLY` | High-trust agents only. |
| `EXPLICIT_USER_CONSENT` | Requires explicit user consent (glacier — paired with thaw). |
| `FILTRATION_AGENT_ONLY` | Only the Filtration agent may touch it (contaminated). |
| `EXPLICIT_USER_CONSENT_OR_USER_DEFINED_CORE_BEHAVIOR` | Sacred: explicit consent, or an agent acting as user-defined core behavior (user proxy). |

### Fail-closed default

`rule_for(reservoir)` returns the matching rule, or — for any reservoir absent
from the policy — a conservative fallback rule (`access_level=APPROVED_AGENTS`,
`user_visible=False`, `usable_for_response=False`). An unknown reservoir therefore
**fails closed** rather than open.

---

## The `check_access` contract

`check_access` (`hydromemory/governance/enforcement.py`) is the single function
recall and every mutating verb call before exposing or changing a droplet.

### Inputs

```python
check_access(
    droplet: Droplet,
    agent: AgentIdentity,
    context: AccessContext,
    operation: Operation,
) -> AccessDecision
```

- **`droplet`** — the target droplet (its `reservoir` selects the rule; its
  `permissions` are the second AND term).
- **`agent: AgentIdentity`** — `name: str`, `trust_level: TrustLevel`
  (default `SESSION`), `is_filtration: bool`, `is_user_proxy: bool`.
- **`context: AccessContext`** — `recall_mode: str | None`, `safe_context: bool`,
  `consent_granted: bool`, `thaw_granted: bool`.
- **`operation: Operation`** — what the agent intends to do.

### The `Operation` set

`Operation` (`hydromemory/governance/obligations.py`):

| Operation | Kind | Gate(s) it triggers |
| --- | --- | --- |
| `READ` | read | reservoir trust + allowlist + (groundwater) explanation + glacier/sacred consent. |
| `EXPOSE_TO_USER` | read | additionally requires `user_visible`; a `private` droplet may only be exposed to its owner (user proxy). |
| `MUTATE` | mutating | trust/allowlist + overwrite-blocked obligation if `overwrite_allowed=false` + external-consent obligation. |
| `TRANSFORM` | mutating | same as `MUTATE`. |
| `OVERWRITE` | mutating | **denied** if the reservoir's `overwrite_allowed=false` (e.g. sacred). |
| `USE_FOR_GENERATION` | read | **denied** if the droplet is not usable for generation (policy or contamination). |

Mutating operations are `{MUTATE, TRANSFORM, OVERWRITE}`.

### Output: `AccessDecision`

```python
AccessDecision(
    allowed: bool,
    denial_reason: str | None,
    obligations: list[Obligation],
    usable_for_generation: bool,
)
```

`usable_for_generation` = `rule.usable_for_response` **AND**
`droplet.meta["usable_for_generation"]` (default `True`). Contamination/lifecycle
steps set the meta flag to `False`; the contaminated reservoir forces it `False`
regardless.

### Decision = reservoir rule AND droplet permissions

The decision ANDs the §10 reservoir rule with the droplet's own `Permissions`.
Evaluation order (a `deny` is returned eagerly; otherwise obligations accumulate
and an `allowed=True` decision is returned at the end):

1. **Contaminated (hard gate).** If `access_level == FILTRATION_AGENT_ONLY`,
   force `usable_for_generation=False`; deny unless `agent.is_filtration`.
2. **Allowlist (hard gate).** Deny if the droplet's `allowed_agents` does not
   admit the agent (see [allowlist rules](#trust-gating-and-the-agent-allowlist)).
3. **Trust floor (hard gate).** Deny if the agent's trust is below the reservoir's
   minimum. (Filtration agents are exempt from the trust floor **only** for the
   contaminated reservoir, which they already cleared in step 1.)
4. **Explanation (obligation).** If `requires_explanation` (groundwater), append
   `REQUIRES_EXPLANATION`.
5. **Glacier (thaw + consent).** If `requires_thaw_protocol`: append
   `REQUIRES_THAW`; append `REQUIRES_CONSENT` if consent is not granted; **deny**
   if `thaw_granted` is false; **deny** if `consent_granted` is false.
6. **Sacred (consent).** If the access level is the sacred level, **deny** with a
   `REQUIRES_CONSENT` obligation unless `consent_granted` or `agent.is_user_proxy`.
7. **Overwrite protection.** For mutating ops, if `overwrite_allowed=false`, append
   `OVERWRITE_BLOCKED`; if the op is specifically `OVERWRITE`, **deny**. (`MUTATE`
   / `TRANSFORM` are allowed but carry the obligation.)
8. **`EXPOSE_TO_USER`.** Deny if the reservoir is not `user_visible`; deny if the
   droplet is `private` and the agent is not a user proxy.
9. **`USE_FOR_GENERATION`.** Deny if `usable_for_generation` is false.
10. **External sharing.** For mutating ops, if `external_sharing` is off and
    `requires_consent_for_external_use` is set without consent, append
    `REQUIRES_CONSENT`. (External-off does **not** by itself block in-vault
    mutation; it surfaces via the consent obligation and the scoring functions.)

### Obligations are returned, not auto-applied

`Obligation` values are **returned** on the decision for the caller (engine/verb)
to satisfy — `check_access` never performs the explanation, thaw, or consent step
itself.

| `Obligation` | Meaning for the caller |
| --- | --- |
| `REQUIRES_EXPLANATION` | Attach an explanation before using groundwater memory. |
| `REQUIRES_THAW` | Run the glacier thaw protocol before proceeding. |
| `REQUIRES_CONSENT` | Obtain explicit user consent (glacier, sacred, or external use). |
| `OVERWRITE_BLOCKED` | This reservoir forbids overwrite; a `MUTATE`/`TRANSFORM` is allowed but the caller is on notice (an `OVERWRITE` is denied outright). |

`AccessDecision.to_dict()` serializes obligations as their string values.

---

## Trust gating and the agent allowlist

### `TrustLevel`

`TrustLevel` (`enforcement.py`) is ordered `session < approved < high_trust`:

| `TrustLevel` | Rank |
| --- | :---: |
| `SESSION` | 0 |
| `APPROVED` | 1 |
| `HIGH_TRUST` | 2 |

Each access level maps to a minimum trust the agent must hold
(`_ACCESS_MIN_TRUST`): `session_agents → SESSION`, `approved_agents → APPROVED`,
`high_trust_agents_only → HIGH_TRUST`. The consent/thaw/filtration access levels
(`explicit_user_consent`, `filtration_agent_only`, the sacred level) carry a
`SESSION` trust floor in the table and are gated by their dedicated consent / thaw
/ filtration checks instead.

### `allowed_agents` and the user-proxy intersection

`_agent_in_permissions` decides whether the droplet's `allowed_agents` admit the
agent:

- A **user-proxy** agent (`is_user_proxy=True`, acting directly for the owner) is
  **always admitted**, regardless of the list.
- An **empty** `allowed_agents` list means "no per-droplet restriction" — any agent
  passes this check (it still has to clear the trust floor).
- A **non-empty** list admits only agents whose `name` is in the list.

Net effect: an agent is admitted iff it is a user proxy, **or** the list is empty,
**or** its name is in the list — intersected with the reservoir's trust floor and
the operation-specific gates above.

---

## Scoring: permission_score and privacy_risk

`hydromemory/governance/scoring.py` provides two continuous companions to the
boolean `check_access`. Both return values in `[0, 1]` and are monotone. The
recall ranker (§5.6) folds them into its ordering: `permission_score` is an
**additive** term and `privacy_risk` is **subtracted** (see
`hydro_recall_score` in `hydromemory/recall.py`).

### `permission_score(droplet, agent) -> float`

How cleanly the agent may **read** the droplet (1.0 clean … 0.0 denied). Evaluated
against a neutral `READ` context (no consent/thaw pre-granted), so it reflects
*standing* access friction.

- If `check_access` denies → **0.0**.
- Otherwise start at **1.0** and subtract per-obligation penalties:
  `REQUIRES_EXPLANATION` −0.10, `REQUIRES_THAW` −0.25, `REQUIRES_CONSENT` −0.25,
  `OVERWRITE_BLOCKED` −0.20 (any other obligation −0.10).
- Add a small trust-headroom reward (+0.02 per rank the agent's trust exceeds the
  reservoir's minimum) so a high-trust agent never scores below an
  exactly-qualified one. Result is clamped to `[0, 1]`.

Recall uses this so cleanly-allowed droplets surface ahead of gated ones.

### `privacy_risk(droplet, context=None) -> float`

Estimated risk of surfacing the droplet (the schema has no first-class
`sensitivity` float, so one is derived). A weighted blend of three monotone
proxies (weights sum to 1.0):

- **Visibility risk** (weight 0.45): `private → 1.0`, `shared → 0.5`,
  `public → 0.1`.
- **Factual uncertainty** (weight 0.25): `1 - confidence`. Per §16, charged-but-
  uncertain memory is riskier to expose; confidence is *not* treated as a proxy
  for emotional charge.
- **Reservoir sensitivity** (weight 0.30): `reservoir_sensitivity(reservoir)` — a
  `[0,1]` proxy derived from §5.3 semantics + §10 restrictiveness. Values:
  working_stream 0.10, surface 0.25, cloud 0.35, ocean 0.55, contaminated 0.60,
  groundwater 0.70, glacier 0.90, sacred 0.95 (unknown → 0.5).

Per-droplet escalators raise the floor: `requires_consent_for_external_use` →
risk ≥ 0.6; `requires_user_review` → risk ≥ 0.5. A `safe_context` recall (the
caller asserting a vetted setting) **damps** the result (× 0.7) — it never raises
it. Result is clamped to `[0, 1]`.

Recall uses this to keep sensitive private memory out of casual exposure.

---

## Contamination

Contamination detection and routing (PRD §10.1) lives in
`hydromemory/contamination.py`. All functions **mutate and return the same
droplet** (no copy), matching the in-place transform style of the forgetting
verbs.

### Detection triggers (§10.1)

A droplet is *polluted* when any of the §10.1 conditions hold:

- the source is unreliable;
- the memory contradicts verified facts;
- the user later corrects it;
- an agent inferred too much;
- the input may be manipulated;
- it is emotionally intense but factually uncertain.

Detection is delegated to an injected `ContaminationDetector` (an ABC in
`hydromemory/intelligence/base.py`) whose `assess` returns a
`ContaminationVerdict(contaminated: bool, reason: str, confidence: float)`. A
deterministic offline stub is the default backend; a Claude-backed detector can be
swapped in.

### `mark_polluted(droplet, reason) -> Droplet`

Stamps a droplet as polluted and routes it to the contaminated reservoir. It:

- sets `phase = Phase.POLLUTED` and `reservoir = Reservoir.CONTAMINATED`;
- records `meta["reason"] = reason`;
- sets `meta["usable_for_generation"] = False` (so `check_access` refuses to use it
  for generation until filtered);
- sets `meta["requires_filtering"] = True`;
- caps `state.confidence` at `0.3` (contaminated memory's confidence is not
  trustworthy).

This mirrors the §10.1 example blob (`phase: polluted`,
`reservoir: contaminated_pool`, `usable_for_generation: false`,
`requires_filtering: true`).

### `assess_and_route(droplet, context, detector) -> Droplet`

Runs the detector, then routes if contaminated:

- always stamps `meta["contamination_checked"] = True` (auditability);
- on a contaminated verdict, calls `mark_polluted(droplet, verdict.reason)` and
  records `meta["contamination_confidence"] = verdict.confidence`;
- a clean verdict leaves the droplet otherwise untouched.

### `filter_droplet(droplet, detector=None) -> Droplet`

The Filtration agent's repair step — flips a polluted droplet back into a usable,
reframed `filtered` droplet:

- sets `phase = Phase.FILTERED`;
- if the droplet is in the `contaminated` reservoir, relocates it to `surface` so
  it can be recalled again (a non-contaminated destination preset by a caller is
  respected);
- raises `state.purity` to at least `FILTERED_PURITY = 0.92` (never lowers an
  already-purer value);
- sets `meta["usable_for_generation"] = True` and
  `meta["requires_filtering"] = False`;
- sets `meta["filtered"] = True`;
- preserves the original pollution `reason` under `meta["reframed_from"]` so the
  repair is auditable.

This is the §12 Example E/F flow: *polluted memory becomes filtered memory*,
re-entering circulation as a softened, qualified statement.

### Agent roles (§8)

Two roles in `hydromemory/agents/` own this surface:

- **Filtration agent** (`agents/filtration.py`) — "Detects contradictions,
  contamination, hallucination, and outdated memory" (§8). It is the **only** role
  that may touch the contaminated reservoir: its identity carries
  `is_filtration=True` and `trust_level=HIGH_TRUST`. For each input droplet it
  either repairs an already-`POLLUTED` droplet (`engine.filter`) or assesses and
  routes a clean one (`engine.assess_and_route`).
- **Privacy agent** (`agents/privacy.py`) — "Controls access, consent, sensitivity,
  and scope" (§8). It owns the governance gate: for exposure it runs `check_access`
  with `Operation.EXPOSE_TO_USER` and computes a `privacy_risk` score the caller
  can threshold. Its identity carries `is_user_proxy=True` and
  `trust_level=HIGH_TRUST`, so it can vet private droplets on the owner's behalf.

See [schema-reference.md](./schema-reference.md) for the droplet, state vector, and
reservoir definitions referenced above.
