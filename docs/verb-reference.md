# HydroMemory Verb Reference

The HydroMemory engine exposes **15 API verbs** (PRD §5.7), a **7-mode recall
layer** (§5.6), and a **7-type forgetting model** (§11). This document is the
catalog for those operations, written against the reference implementation in
`hydromemory/verbs.py`, `hydromemory/recall.py`, and `hydromemory/forgetting.py`.

> Co-ownership note: the verbs themselves live in `hydromemory/verbs.py`, but
> several of them **delegate** their policy to neighbouring modules that are
> documented elsewhere:
>
> - `FREEZE` / `FORGET` -> governance `check_access` (see the governance-policy
>   reference).
> - `FILTER` / `POLLUTE` -> the contamination module.
> - `DRAIN` / `ARCHIVE` / `FORGET` -> the forgetting module (`hydromemory/forgetting.py`).
>
> The schema types referenced below (`Droplet`, `State`, `Phase`, `Reservoir`,
> `Retention`, `ProtocolEnvelope`, `ProtocolResponse`) are defined in the
> schema reference.

All `Verbs` methods are instance methods on the `Verbs` dataclass, which is
constructed with its dependencies injected (`repo`, `intelligence`,
`check_access`, `forgetting`, `contamination`, `permission_score`,
`privacy_risk`, `phase_config`). Each verb returns either a `Droplet` or a
`ProtocolResponse` (the recall/query-shaped verbs), whichever is the natural
unit for that operation.

---

## 1. The 15 verbs (PRD §5.7)

| # | Verb | Purpose | PRD §5.7 description |
|---|------|---------|---------------------|
| 1 | `ABSORB` | Create a memory droplet from experience | Create a memory droplet from experience. |
| 2 | `FLOW` | Connect a memory to related memories | Connect memory to related memories. |
| 3 | `EVAPORATE` | Abstract a memory into a pattern | Abstract a memory into a pattern. |
| 4 | `CONDENSE` | Cluster related abstracted memories | Cluster related abstracted memories. |
| 5 | `PRECIPITATE` | Recall a memory into active use | Recall a memory into active use. |
| 6 | `INFILTRATE` | Move a memory into deep storage | Move memory into deep storage. |
| 7 | `FREEZE` | Preserve a memory as a high-integrity snapshot | Preserve memory as high-integrity snapshot. |
| 8 | `MELT` | Reactivate a preserved memory | Reactivate preserved memory. |
| 9 | `FILTER` | Clean, verify, reconcile, or correct a memory | Clean, verify, reconcile, or correct memory. |
| 10 | `POLLUTE` | Mark a memory as contaminated or untrusted | Mark memory as contaminated or untrusted. |
| 11 | `DISTILL` | Extract the purest principle from a cluster | Extract the purest principle from a cluster. |
| 12 | `IRRIGATE` | Apply a memory pattern to a new task | Apply a memory pattern to a new task. |
| 13 | `DRAIN` | Reduce salience or active influence | Reduce salience or active influence. |
| 14 | `ARCHIVE` | Move a memory to cold storage | Move to cold storage. |
| 15 | `FORGET` | Delete or render a memory inaccessible | Delete or render inaccessible by policy. |

The reservoirs and phases named below are the canonical enum values from
`hydromemory/reservoirs.py` (`Reservoir`) and `hydromemory/schema.py` (`Phase`).

---

## 2. Verb catalog

### 1. `ABSORB`

```python
def absorb(
    self,
    content: str,
    *,
    source: str = "experience",
    context: dict[str, Any] | None = None,
    reservoir: Reservoir | str = Reservoir.WORKING_STREAM,
    state: State | None = None,
    envelope: ProtocolEnvelope | None = None,
) -> Droplet
```

Encodes, classifies, and stores a brand-new droplet. The content is embedded
(`intelligence.embedder.embed`) and classified (`intelligence.classifier.classify`);
the classifier's `memory_type`, `importance`, `sensitivity`, and
`expected_lifespan` are recorded (the latter three under `droplet.meta`). If a
§6 `ProtocolEnvelope` is supplied, its `input` / `initial_state` blocks seed the
droplet (content, source, context, and reservoir).

- **Phase:** `LIQUID` (a fresh memory is always liquid; this is the
  `Experience -> Liquid` entry of the §5.4 chain).
- **Reservoir:** the supplied `reservoir` (default `working_stream`), normalized
  via `normalize_reservoir`.
- **State:** the caller's `state` (or a fresh zeroed `State`).
- **Delegation:** none. Persists via `repo.upsert`.

### 2. `FLOW`

```python
def flow(self, droplet: Droplet, related_ids: Sequence[str], *, kind: str = "associations") -> Droplet
```

Adds association links (or another `kind`, e.g. `supports`, `contradictions`,
`derived_from`) from `droplet` to each related id. Self-links are skipped. Both
the persisted store (`repo.add_link`) and the in-memory `droplet.links.<kind>`
list are updated.

- **Phase / Reservoir:** unchanged.
- **State:** unchanged.
- **Delegation:** none. (Association links later make the `ASSOCIATION` trigger
  fire, which can drive `RAIN -> RIVER`.)

### 3. `EVAPORATE`

```python
def evaporate(self, droplet: Droplet) -> Droplet
```

Abstracts the droplet into a **new** `VAPOR` droplet (the original is left
intact). The essence text comes from `intelligence.abstractor.evaporate`; the
new droplet links back to the source via `derived_from`.

- **Phase:** the new droplet is `VAPOR`.
- **Reservoir:** the new droplet lands in `cloud`.
- **State (new droplet):** `temperature +0.2`, `fluidity +0.1`; `purity` and
  `confidence` copied from the source (all clamped to `[0, 1]`). The essence is
  stored as `meta["pattern"]`.
- **Delegation:** none.

> Note: the verb `EVAPORATE` (abstraction into a new vapor droplet) is distinct
> from the forgetting function `forgetting.evaporate` (§11 Evaporation
> Forgetting), which mutates a droplet in place to fade its detail. See §4.

### 4. `CONDENSE`

```python
def condense(self, vapors: Sequence[Droplet], *, theme: str | None = None) -> Droplet
```

Clusters one or more `VAPOR` droplets into a single new `CLOUD` droplet. Raises
`ValueError` if `vapors` is empty. The content is the supplied `theme` (or the
members' contents joined with `; `). Member ids are recorded in
`meta["members"]` and each member is linked via `derived_from`.

- **Phase:** the new droplet is `CLOUD`.
- **Reservoir:** `cloud`.
- **State (new droplet):** `pressure` = mean member pressure `+0.1`;
  `confidence` and `purity` = mean of members (clamped).
- **Delegation:** none.

### 5. `PRECIPITATE`

```python
def precipitate(
    self,
    query: str,
    *,
    agent: Any,
    query_ctx: dict[str, Any] | None = None,
    context: Any = None,
    k: int = 10,
    weights: RecallWeights | None = None,
) -> ProtocolResponse
```

The **recall path**. Embeds the `query`, searches the store for the `k` most
semantically similar droplets (gated so only droplets the agent has permission
to see are candidates), scores each with `hydro_recall_score` (§5.6), drops any
whose score is at or below `recall_threshold(phase, reservoir)`, picks a recall
mode with `select_recall_mode`, and renders each survivor with `format_recall`.

- **Phase / Reservoir / State:** read-only — `PRECIPITATE` does **not** mutate
  the matched droplets.
- **Returns:** a `ProtocolResponse` with `operation="PRECIPITATE"` whose
  `result` is a list of `RecallResult` objects sorted by `score` descending, and
  an `outcome` of `{candidates, recalled, agent}`.
- **Delegation:** scoring/mode selection delegate to `hydromemory.recall`;
  permission scoring uses the injected `permission_score` (default falls back to
  `allowed_agents`/visibility). The contamination penalty passed in is
  `1 - state.purity`. See §3 for the recall modes.

### 6. `INFILTRATE`

```python
def infiltrate(self, droplet: Droplet, *, context: dict[str, Any] | None = None) -> Droplet
```

Sinks a memory toward deep storage. If the droplet is a `RIVER`, it drives the
§5.4 chain via the `REPETITION` trigger (`apply_phase_transition`); otherwise it
deepens the droplet directly.

- **Phase:** a `RIVER` may advance to `GROUNDWATER` via `REPETITION`; a `LIQUID`
  droplet is set to `GROUNDWATER` directly. Other phases are deepened without a
  forced phase change.
- **Reservoir:** ends in `groundwater` whenever the droplet is (or becomes)
  `GROUNDWATER`.
- **State:** the direct path raises `depth +0.3` and `gravity +0.1` (clamped);
  the `REPETITION` transition applies its own table effects (`depth +0.3`,
  `gravity +0.1`, `temperature -0.2`, `fluidity -0.2`). The context's
  `cycle_count` is floored to `phase_config.repetition_cycles` so the repetition
  guard passes.
- **Delegation:** phase transition logic in `hydromemory.phases`.

### 7. `FREEZE`

```python
def freeze(self, droplet: Droplet, *, agent: Any = None, context: Any = None) -> Droplet
```

Preserves a memory as a high-integrity `ICE` snapshot in the glacier.
**Co-owned:** when both `check_access` and `agent` are supplied, the identity-write
policy review is delegated to governance with `Operation.OVERWRITE`. If access
is denied, the droplet is returned **unchanged** (no snapshot is written) and
`meta["freeze_denied"]` records the reason.

- **Phase:** `ICE` (on success).
- **Reservoir:** `glacier` (on success).
- **State:** `integrity +0.2`, `temperature -0.4`, `fluidity -0.5` (clamped);
  `cycle.last_transformed` stamped.
- **Delegation:** governance `check_access` (`Operation.OVERWRITE`).

### 8. `MELT`

```python
def melt(self, droplet: Droplet, *, context: dict[str, Any] | None = None) -> Droplet
```

Thaws an `ICE` snapshot back to active use, but **only** when the context is
safe. If the droplet is not `ICE`, it is returned unchanged. If the context is
not safe (`context["safe_context"]` or `context["safe"]` truthy), the droplet is
returned unchanged with `meta["melt_blocked"]` set.

- **Phase:** `ICE -> LIQUID` via the `SAFE_CONTEXT` trigger (on success).
- **Reservoir:** `working_stream` (on success).
- **State:** the `SAFE_CONTEXT` transition applies `temperature +0.3`,
  `fluidity +0.4` (clamped).
- **Delegation:** phase transition logic in `hydromemory.phases`.

### 9. `FILTER`

```python
def filter(self, droplet: Droplet) -> Droplet
```

Cleans / verifies / reconciles a memory. **Co-owned:** delegates entirely to
`contamination.filter_droplet(droplet, detector=self.intelligence.detector)`,
then persists the result. Raises `RuntimeError` if no contamination module was
injected.

- **Phase / Reservoir / State:** determined by the contamination module's policy
  (typically `POLLUTED -> FILTERED`); not set by the verb itself.
- **Delegation:** the contamination module.

### 10. `POLLUTE`

```python
def pollute(self, droplet: Droplet, reason: str) -> Droplet
```

Marks a memory as contaminated / untrusted. **Co-owned:** delegates entirely to
`contamination.mark_polluted(droplet, reason)`, then persists. Raises
`RuntimeError` if no contamination module was injected.

- **Phase / Reservoir / State:** determined by the contamination module's policy
  (typically the `POLLUTED` phase / `contaminated` pool); not set by the verb
  itself.
- **Delegation:** the contamination module.

### 11. `DISTILL`

```python
def distill(self, cluster: Sequence[Droplet], *, principle: str | None = None) -> Droplet
```

Extracts a single high-purity **principle** droplet from a cluster. Raises
`ValueError` if `cluster` is empty. The principle text is the supplied
`principle` or is derived from the joined member contents via
`intelligence.abstractor.evaporate`. Member ids are recorded in
`meta["distilled_from"]` and linked via `derived_from`.

- **Phase:** the new droplet is `GROUNDWATER`.
- **Reservoir:** `sacred` (a declared principle / identity anchor).
- **State (new droplet):** `purity` = max member purity `+0.05`; `gravity` = max
  member gravity `+0.1`; `integrity` = max member integrity; `confidence` = mean
  member confidence; `depth = 0.6` (all clamped).
- **Delegation:** none (uses the abstractor for text only).

### 12. `IRRIGATE`

```python
def irrigate(self, droplet: Droplet, *, task: str | None = None) -> Droplet
```

Applies a memory pattern to a new task and records the usage. Increments the
droplet's cycle count (`repo.touch_cycle(..., increment_count=True)` and the
in-memory `cycle.cycle_count`), stamps `cycle.last_recalled`, and appends the
`task` to `meta["applied_to"]` when one is given.

- **Phase / Reservoir:** unchanged.
- **State:** unchanged (only the `cycle` block changes). Repeated irrigation
  raises `cycle_count`, which can later fire the `REPETITION` trigger.
- **Delegation:** none.

### 13. `DRAIN`

```python
def drain(self, droplet: Droplet, **kwargs: Any) -> Droplet
```

Reduces a memory's active influence. **Co-owned:** delegates entirely to
`forgetting.drain(droplet, **kwargs)`, then persists. Raises `RuntimeError` if
no forgetting module was injected.

- **Phase / Reservoir:** unchanged (content and reservoir are left intact).
- **State:** per `forgetting.drain` — `pressure` and `fluidity` driven to `0.0`,
  `temperature *0.3`, with `meta["active"]=False`. See §4 (Drainage Forgetting).
- **Delegation:** the forgetting module.

### 14. `ARCHIVE`

```python
def archive(self, droplet: Droplet, *, seal: bool = False, **kwargs: Any) -> Droplet
```

Moves a memory to cold storage. **Co-owned:** delegates to
`forgetting.seal(droplet, ...)` when `seal=True`, otherwise to
`forgetting.sediment(droplet, ...)`, then persists. Raises `RuntimeError` if no
forgetting module was injected.

- **Phase / Reservoir / State (default, `sediment`):** reservoir -> `groundwater`,
  phase -> `GROUNDWATER`, retention -> `ARCHIVED`, `depth` raised, `fluidity`
  lowered. See §4 (Sedimentation).
- **Phase / Reservoir / State (when `seal=True`):** reservoir -> `glacier`,
  phase -> `ICE`, `fluidity`/`temperature` zeroed, `meta["sealed"]=True` /
  `meta["accessible"]=False`. See §4 (Sealing).
- **Delegation:** the forgetting module (`sediment` or `seal`).

### 15. `FORGET`

```python
def forget(self, droplet: Droplet, *, agent: Any = None, context: Any = None) -> ProtocolResponse
```

Deletes a memory, **governance-checked**. **Co-owned:** when both `check_access`
and `agent` are supplied, runs a policy review with `Operation.MUTATE` first; if
denied, nothing is deleted. On approval (or when no governance check is
configured), calls `forgetting.delete(droplet)` and `repo.delete(droplet.id)`.
Raises `RuntimeError` if no forgetting module was injected.

- **Phase / Reservoir / State:** N/A on success — the droplet row is removed
  from the store.
- **Returns:** a `ProtocolResponse` with `operation="FORGET"`, `result` (bool),
  the governance `decision` (when one was made), and an `outcome` of
  `{deleted, droplet_id}`.
- **Delegation:** governance `check_access` (`Operation.MUTATE`) **and** the
  forgetting module (`forgetting.delete`).

---

## 3. Recall modes (PRD §5.6)

Recall is more than semantic similarity. `PRECIPITATE` scores each candidate with
the §5.6 formula (implemented as `hydro_recall_score`):

```
recall_score =
    semantic_similarity
  + trigger_similarity
  + contextual_fit
  + pressure
  + gravity
  + phase_accessibility
  + permission_score
  - depth_resistance
  - contamination_penalty
  - privacy_risk
```

Every term is clamped to `[0, 1]` and weighted by `RecallWeights` (all default
`1.0`). The first four positive terms plus `phase_accessibility` and
`permission_score` add; the final three subtract. A candidate is recalled only
when its score **exceeds** `recall_threshold(phase, reservoir)` (a per-phase base
plus a small per-reservoir additive adjustment — deeper/frozen/polluted phases
and slower reservoirs demand a higher bar).

Once a droplet passes the threshold, `select_recall_mode` chooses how it surfaces.

### The 7 `RecallMode` values

| `RecallMode` | Value | §5.6 use |
|--------------|-------|----------|
| `LITERAL` | `literal` | Use exact stored content. |
| `PATTERN` | `pattern` | Use abstracted meaning. |
| `BEHAVIORAL` | `behavioral` | Adapt behavior without quoting the memory. |
| `WARNING` | `warning` | Surface a risk, contradiction, or boundary. |
| `SILENT` | `silent` | Guide behavior without explicitly mentioning the memory. |
| `USER_VISIBLE` | `user_visible` | Tell the user what memory is being used. |
| `REFLECTIVE` | `reflective` | Ask whether the memory is still accurate. |

### How a mode is selected (`select_recall_mode`)

Rule-based, **first match wins**:

1. **`WARNING`** — the droplet is `POLLUTED`, in the `contaminated` reservoir,
   has `contradictions` links, or the context flags a contradiction, or
   `meta["requires_filtering"]` is set.
2. **`SILENT`** — sensitivity `>= 0.7` **and** the memory is `private`.
3. **`REFLECTIVE`** — `state.confidence <= 0.3`, or the phase is `FOG`
   (ambiguous recall).
4. **`USER_VISIBLE`** — the context explicitly asks what is known
   (`what_do_you_know` / `user_visible` / `show_memory` truthy, or
   `intent` is `what_do_you_know` / `list_memory`).
5. **`LITERAL`** — an exact-quote request (`exact_quote` / `verbatim` / `literal`
   truthy, or `intent` is `exact_quote` / `quote`).
6. **`PATTERN`** — abstract phases (`VAPOR` or `CLOUD`).
7. **`BEHAVIORAL`** — the default for identity-relevant / actionable recall.

### How output is shaped (`RecallResult`)

`format_recall` renders the chosen droplet into a `RecallResult`. The fields are:

| Field | Type | Meaning |
|-------|------|---------|
| `mode` | `RecallMode` | The selected mode. |
| `surface_text` | `str` | Text intended for the user (empty for modes that do not surface text, e.g. `BEHAVIORAL`, `SILENT`). |
| `internal_guidance` | `str` | How the agent should use the memory internally. |
| `show_to_user` | `bool` | Whether `surface_text` should be shown (`True` for `LITERAL`, `WARNING`, `USER_VISIBLE`, `REFLECTIVE`; `False` for `PATTERN`, `BEHAVIORAL`, `SILENT`). |
| `explanation` | `str` | A short human-readable reason for the chosen mode. |
| `droplet_id` | `str` | The source droplet id. |
| `score` | `float` | The recall score that selected it. |
| `meta` | `dict` | Optional extra detail. |

---

## 4. Forgetting model (PRD §11)

The §11 forgetting model is implemented in `hydromemory/forgetting.py`. Each
function is a pure droplet transform that mutates and returns the same `Droplet`
— except `delete`, which returns `None` to signal the store should drop the row.

| §11 type | `forgetting.py` function | PRD §11 meaning | Invoked by verb | State / phase / reservoir change |
|----------|--------------------------|-----------------|-----------------|----------------------------------|
| Evaporation Forgetting | `evaporate(droplet)` | Details fade but pattern remains. | (forgetting-layer; not a 1:1 verb — distinct from the `EVAPORATE` verb) | `LIQUID -> VAPOR` (non-liquid phases kept); `fluidity *0.4`, `depth *0.6`, `temperature *0.5`; pre-fade content kept in `meta["gist"]`; `meta["evaporated"]=True`. |
| Drainage Forgetting | `drain(droplet)` | Memory loses active influence. | `DRAIN` | Phase/reservoir/content unchanged; `pressure=0.0`, `fluidity=0.0`, `temperature *0.3`; `meta["active"]=False`, `meta["drained"]=True`. |
| Sedimentation | `sediment(droplet)` | Memory sinks into the archive. | `ARCHIVE` (default, `seal=False`) | reservoir -> `groundwater`, phase -> `GROUNDWATER`, retention -> `ARCHIVED`; `depth` raised (`max(depth, 0.5)+0.3`), `fluidity *0.3`; `meta["sedimented"]=True`. |
| Dissolution | `dissolve(droplet, into_id)` | Memory merges into a broader pattern. | (forgetting-layer; not a 1:1 verb) | Phase/reservoir unchanged; `meta["merged_into"]=<id>`, `meta["dissolved"]=True`, `into_id` added to `links.derived_from`; `integrity *0.5`, `fluidity *0.5`. |
| Deletion | `delete(droplet)` | Memory is removed by user command. | `FORGET` (after governance approval) | Hard removal — returns `None`; the store drops the row keyed by `droplet.id`. |
| Sealing | `seal(droplet)` | Memory remains stored but inaccessible. | `ARCHIVE` (with `seal=True`) | reservoir -> `glacier`, phase -> `ICE`; `fluidity=0.0`, `temperature=0.0`; `meta["sealed"]=True`, `meta["accessible"]=False` (high integrity preserved). |
| Composting | `compost(droplet, lesson)` | Memory becomes a lesson; original detail is discarded. | (forgetting-layer; not a 1:1 verb) | Phase/reservoir unchanged; `content` replaced by `lesson`, original kept in `meta["composted_from"]`, `meta["original_detail_discarded"]=True`; `depth` raised (`max(depth, 0.6)+0.2`), `purity` raised (`max(purity, 0.8)`). |

> The verbs `DRAIN`, `ARCHIVE`, and `FORGET` are the public entry points to the
> forgetting layer (see §2). `evaporate`, `dissolve`, and `compost` are
> forgetting-model functions exercised by the engine/pipeline rather than mapped
> one-to-one onto a single public verb. Both are documented here for
> completeness; the forgetting module is co-owned with the §11 governance
> material.

---

## 5. Phase transitions (§5.4) and triggers (§5.5)

Phases describe what a memory *is* right now; triggers are the *forces* that move
it between phases. The §5.4 transition chain (implemented as a data-driven table
in `hydromemory/phases.py`) is:

```
Experience -> Liquid                         (entry)
Liquid    + HEAT            -> Vapor
Vapor     + SIMILARITY      -> Cloud
Cloud     + DENSITY (+trig) -> Rain
Rain      + ASSOCIATION     -> River
River     + REPETITION      -> Groundwater
Liquid    + EXTREME_CHARGE  -> Ice
Ice       + SAFE_CONTEXT    -> Liquid
Polluted  + FILTRATION      -> Filtered
Filtered  + REINTEGRATION   -> Liquid / Groundwater   (dynamic: high gravity sinks to groundwater)
```

`apply_phase_transition(droplet, trigger, context, config)` looks up the row
matching `(droplet.phase, trigger)` whose guard passes, mutates the phase
(honouring a `target_fn` for the dynamic FILTERED reintegration), applies the
row's additive `effects` (clamped to `[0, 1]`), and stamps
`cycle.last_transformed`. When several triggers fire at once,
`apply_phase_transitions` orders them by `TRIGGER_PRIORITY` so protective /
structural transitions win (e.g. a fresh `LIQUID` droplet with both
`EXTREME_CHARGE` and `HEAT` freezes to `ICE` rather than merely evaporating).

Guard thresholds live in `PhaseConfig` (documented defaults): `density_threshold
= 0.6`, `extreme_charge_threshold = 0.85`, `repetition_cycles = 3`,
`groundwater_gravity_threshold = 0.7`.

### Triggers (§5.5)

`detect_triggers(droplet, context, config)` maps a droplet's `State` floats plus
a free-form context dict to the set of fired triggers. Two families:

- **Natural forces** (§5.5): `HEAT` (attention, novelty, emotional activation),
  `PRESSURE` (urgency, stakes, tension), `GRAVITY` (importance, consequence,
  identity relevance), `WIND` (social input, communication), `TERRAIN` (user
  personality, platform/environment), `SALT` (emotional residue, bias, symbolic
  meaning), `COLD` (preservation, silence, reflection), `STORM` (crisis,
  conflict, rapid change), `FILTRATION` (verification, correction, evidence,
  reasoning), `POLLUTION` (misinformation, contradiction, manipulation, noise).
- **Synthetic / engine-emitted triggers** (complete the §5.4 chain):
  `SIMILARITY`, `ASSOCIATION`, `REPETITION`, `DENSITY`, `EXTREME_CHARGE`,
  `SAFE_CONTEXT`, `REINTEGRATION`.

Detection thresholds live in `TriggerConfig` (documented defaults; most natural
forces fire at `>= 0.6` on their state float, `EXTREME_CHARGE` at
`emotional_charge >= 0.85`, and `REPETITION` at `cycle_count >= 3`).

How verbs ride this machinery: `ABSORB` performs the `Experience -> Liquid`
entry; `INFILTRATE` drives `REPETITION` (settling a `RIVER` into `GROUNDWATER`);
`FREEZE`/`MELT` mirror the `EXTREME_CHARGE`/`SAFE_CONTEXT` ice transitions; and
`FILTER` operates on the `POLLUTED -> FILTERED` path.
