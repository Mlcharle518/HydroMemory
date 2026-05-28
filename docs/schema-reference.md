# Schema Reference

The canonical data model of the HydroMemory Protocol reference implementation.
This document describes what the code in `hydromemory/schema.py` and
`hydromemory/reservoirs.py` actually implements. Where the implementation differs
from the PRD, **the code is the source of truth** and the difference is called
out explicitly.

A **memory droplet** is the atomic unit of HydroMemory (PRD §5.2, §7). It carries
content plus the metadata that drives the hydraulic lifecycle: a phase, a
reservoir, a physics-like state vector, permissions, links to other droplets, and
cycle bookkeeping.

- [The Droplet](#the-droplet)
- [State vector](#state-vector)
- [Phase](#phase)
- [Reservoir](#reservoir)
- [Permissions](#permissions)
- [Links](#links)
- [Cycle](#cycle)
- [Serialization](#serialization)
- [Canonical JSON example](#canonical-json-example)

---

## The Droplet

`Droplet` is a dataclass (`hydromemory/schema.py`). The PRD's "Minimum Memory
Schema" (§7) defines a subset of these fields; the implementation adds a few
first-class fields the spec describes elsewhere (§5.2, §6) but omits from §7.

| Field | Type | Default | Meaning | PRD |
| --- | --- | --- | --- | --- |
| `id` | `str` | generated `mem_<8 hex>` | Stable unique identifier for the droplet. | §7 (`id`) |
| `content` | `str` | `""` | The raw remembered content. | §7 |
| `source` | `str` | `"unknown"` | Where the memory came from (conversation, document, file, etc.). | §7 |
| `created_at` | `datetime` (tz-aware, UTC) | now | Creation timestamp. Naive datetimes are coerced to UTC. | §7 |
| `phase` | `Phase` | `Phase.LIQUID` | Current lifecycle phase (see [Phase](#phase)). | §5.4, §7 |
| `reservoir` | `Reservoir` | `Reservoir.WORKING_STREAM` | Storage layer the droplet lives in (see [Reservoir](#reservoir)). | §5.3, §7 |
| `memory_type` | `str \| None` | `None` | Classification label (e.g. `preference`, `conceptual_preference`, `cognitive_style`). Produced by the §6 classification block. | §5.2, §6 |
| `semantic_tags` | `list[str]` | `[]` | Free-form topical tags used by recall's contextual-fit term. | §5.2 |
| `state` | `State` | zero vector | The physics-like state vector (see [State vector](#state-vector)). | §5.2, §7 |
| `permissions` | `Permissions` | defaults | Access / governance metadata (see [Permissions](#permissions)). | §5.2, §7, §10 |
| `links` | `Links` | empty | Relationships to other droplets (see [Links](#links)). | §7 |
| `cycle` | `Cycle` | zero/`None` | Lifecycle bookkeeping (see [Cycle](#cycle)). | §7 |
| `meta` | `dict[str, Any]` | `{}` | Open extension bag. Holds derived/lifecycle keys (e.g. `usable_for_generation`, `requires_filtering`, `reason`, `pattern`, `triggers`, `context`) and any unknown top-level keys preserved on ingest. | implementation |
| `embedding` | `list[float] \| None` | `None` | Optional semantic-similarity vector. Excluded from `to_dict()` unless explicitly requested. | implementation (§5.6 backend) |

### Fields that are first-class beyond §7

The §7 "Minimum Memory Schema" lists `id, content, source, created_at, phase,
reservoir, state, permissions, links, cycle`. The implementation promotes these
additional fields to first-class members because other PRD sections rely on them:

- **`memory_type`** — the §6 classification block (`"memory_type"`) and the §5.2
  droplet example (`"type"`) both carry a type label. The code stores it as
  `memory_type` and accepts `type` as an ingest alias.
- **`semantic_tags`** — present in the §5.2 droplet example (`"semantic_tags"`).
  Recall's `contextual_fit` term reads it, so it is a typed list rather than a
  loose `meta` key.
- **`meta`** and **`embedding`** — implementation conveniences. `meta` preserves
  spec blob keys that have no dedicated field (e.g. the §10.1 `reason` /
  `usable_for_generation` / `requires_filtering`); `embedding` backs the optional
  Claude/embeddings recall backend (§5.6).

---

## State vector

`State` (`hydromemory/schema.py`) is the physics-like state vector. The PRD §7
schema defines **nine** floats; the implementation adds **`emotional_charge`** as
a tenth (it appears in the §5.2 droplet example and §12 Example A). **Every value
is constrained to `[0, 1]`** via `clamp_unit`; `State.clamped()` returns a clamped
copy.

| Field | Range | Meaning | PRD |
| --- | --- | --- | --- |
| `temperature` | `[0,1]` | Activation / attention energy — how "hot" and active the memory is (heat → attention, novelty, emotional activation, §5.5). | §7 |
| `pressure` | `[0,1]` | Urgency / unresolved tension pushing the memory toward recall. Adds to the recall score. | §7 |
| `gravity` | `[0,1]` | Importance, consequence, identity relevance — the memory's pull. Adds to the recall score. | §7 |
| `purity` | `[0,1]` | Cleanliness / trustworthiness. `1 - purity` is the default `contamination_penalty` in recall; filtration raises purity to ≥ 0.92. | §7 |
| `salinity` | `[0,1]` | Accumulated emotional residue, bias, or symbolic loading (salt → bias/residue, §5.5). | §7 |
| `depth` | `[0,1]` | How deep / latent the memory is. Used directly as `depth_resistance` (subtracted from the recall score). | §7 |
| `fluidity` | `[0,1]` | How readily the memory flows / transforms between phases. | §7 |
| `integrity` | `[0,1]` | Structural soundness of the record (high for frozen/ice snapshots). | §7 |
| `confidence` | `[0,1]` | Factual confidence. Low confidence drives reflective recall and raises privacy risk. **Not** a proxy for emotional charge (§16). | §7 |
| `emotional_charge` | `[0,1]` | Emotional intensity. First-class in the code (PRD §5.2); accepts `charge` as an ingest alias (§12 Example A). | §5.2 |

The exact field tuples are defined in code as:

- `CANONICAL_STATE_FIELDS` — the nine §7 floats.
- `STATE_FIELDS` — `CANONICAL_STATE_FIELDS + ("emotional_charge",)`.

> **Difference from PRD §7:** §7 enumerates nine state floats and does not list
> `emotional_charge`. The implementation treats `emotional_charge` as a canonical
> tenth state float (per §5.2) and is the source of truth. `charge` is accepted on
> ingest and normalized to `emotional_charge`.

---

## Phase

`Phase` (`hydromemory/schema.py`) is a string enum with **all 13** values from the
§5.4 Phase Transformation Layer:

| Phase | Value | Meaning (§5.4) | Storable? |
| --- | --- | --- | --- |
| `LIQUID` | `liquid` | Active, flexible, usable memory. | yes |
| `VAPOR` | `vapor` | Abstracted pattern or essence. | yes |
| `CLOUD` | `cloud` | Cluster of related abstractions. | yes |
| `RAIN` | `rain` | Recalled memory entering active context. | yes |
| `RIVER` | `river` | Associative flow chain. | **transient** |
| `GROUNDWATER` | `groundwater` | Deep latent memory. | yes |
| `ICE` | `ice` | Preserved high-integrity snapshot. | yes |
| `SNOW` | `snow` | Soft preservation, less rigid than ice. | **transient** |
| `FOG` | `fog` | Ambiguous or partial recall. | **transient** |
| `STEAM` | `steam` | High-energy active abstraction. | **transient** |
| `OCEAN` | `ocean` | Merged collective or global pattern. | yes |
| `POLLUTED` | `polluted` | Corrupted, distorted, or low-purity memory. | yes |
| `FILTERED` | `filtered` | Cleaned, verified, reconciled memory. | yes |

### Storable vs. transient phases

`STORABLE_PHASES` is the frozenset of **nine** phases that §7 allows for a
persisted droplet (`liquid | vapor | cloud | rain | groundwater | ice | ocean |
polluted | filtered`). The remaining four are derived recall/lifecycle states that
are never stored:

`TRANSIENT_PHASES` = `{river, snow, fog, steam}` (computed in code as
`all phases − STORABLE_PHASES`).

These transient phases still appear during recall scoring (they are assigned
`phase_accessibility` and threshold values in `hydromemory/recall.py`), but a
persisted droplet's `phase` is expected to be one of the nine storable values.

---

## Reservoir

`Reservoir` (`hydromemory/reservoirs.py`) is a string enum of the **eight** storage
layers from §5.3. Each reservoir has behavioral metadata in `RESERVOIR_BEHAVIOR`
(`speed` in `[0,1]`, `volatile` flag, `description`). `speed` feeds recall's
`phase_accessibility` term — higher means faster, more readily recalled. Access
governance lives separately in `hydromemory/governance` (see
[governance-policy.md](./governance-policy.md)).

| Reservoir | Value | Function (§5.3) | `speed` | `volatile` |
| --- | --- | --- | --- | --- |
| `WORKING_STREAM` | `working_stream` | Immediate active context; session-oriented. | 1.0 | yes |
| `SURFACE` | `surface` | Recently used memories and near-term associations. | 0.8 | no |
| `GROUNDWATER` | `groundwater` | Persistent user patterns and identity-level structures. | 0.4 | no |
| `GLACIER` | `glacier` | Frozen high-integrity records and sensitive snapshots; requires thaw. | 0.2 | no |
| `CLOUD` | `cloud` | Abstracted pattern clusters; useful for distillation. | 0.6 | no |
| `OCEAN` | `ocean` | Collective or generalized knowledge layer; strong privacy boundaries. | 0.3 | no |
| `CONTAMINATED` | `contaminated` | Unverified, contradictory, or unsafe memory; not usable until filtered. | 0.0 | no |
| `SACRED` | `sacred` | User-declared values, vows, principles, identity anchors; not casually overwritten. | 0.5 | no |

### Reservoir name aliases

§5.3 / §6 / §10 use longer display names for several reservoirs. `normalize_reservoir`
maps these (case-insensitively, trimmed) to the canonical enum values via
`RESERVOIR_ALIASES`:

| Alias (spec display name) | Canonical value |
| --- | --- |
| `surface_reservoir` | `surface` |
| `cloud_layer` | `cloud` |
| `contaminated_pool` | `contaminated` |
| `sacred_spring` | `sacred` |
| `stream` | `working_stream` |
| `working` | `working_stream` |

> **Note:** the canonical stored values are the short forms (`surface`, `cloud`,
> `contaminated`, `sacred`, `working_stream`). The longer forms are accepted on
> ingest and emitted by the spec, but `to_dict()` always writes the short form.

---

## Permissions

`Permissions` (`hydromemory/schema.py`) carries the access / governance metadata.
The §7 schema lists `owner, visibility, allowed_agents, retention,
external_sharing`; the implementation adds two consent flags that appear in the
§5.2 / §6 examples (`requires_consent_for_external_use`, `requires_user_review`).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `owner` | `str` | `"user"` | The principal that owns the droplet. |
| `visibility` | `Visibility` | `PRIVATE` | `private` \| `shared` \| `public`. Private droplets may only be exposed to their owner. |
| `allowed_agents` | `list[str]` | `[]` | Per-droplet agent allowlist. An **empty list means no per-droplet restriction**; a non-empty list restricts access to the named agents (plus any user-proxy agent). |
| `retention` | `Retention` | `TEMPORARY` | `temporary` \| `persistent` \| `archived`. |
| `external_sharing` | `bool` | `False` | Whether the droplet may leave the vault. |
| `requires_consent_for_external_use` | `bool` | `False` | External use requires explicit user consent (raises privacy risk; adds a consent obligation on mutating ops). First-class in code (§5.2). |
| `requires_user_review` | `bool` | `False` | The droplet should be reviewed by the user before use (raises privacy risk). First-class in code. |

`Visibility` and `Retention` are string enums:

- `Visibility`: `PRIVATE = "private"`, `SHARED = "shared"`, `PUBLIC = "public"`.
- `Retention`: `TEMPORARY = "temporary"`, `PERSISTENT = "persistent"`, `ARCHIVED = "archived"`.

> **Difference from PRD §7:** §7's permissions block omits
> `requires_consent_for_external_use` and `requires_user_review`. The
> implementation includes both as first-class booleans (they are present in the
> §5.2 and §6 example blobs) and is the source of truth.

---

## Links

`Links` (`hydromemory/schema.py`) holds typed relationships to other droplets by
id. All four fields are `list[str]` defaulting to empty (§7).

| Field | Meaning |
| --- | --- |
| `associations` | Associative connections (fast runoff chains). |
| `contradictions` | Droplets this one contradicts. A non-empty list drives **warning** recall. |
| `supports` | Droplets this one supports / corroborates. |
| `derived_from` | Provenance — droplets this one was distilled/abstracted from. |

---

## Cycle

`Cycle` (`hydromemory/schema.py`) records lifecycle bookkeeping (§7). Timestamps
are tz-aware (UTC) or `None`.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `cycle_count` | `int` | `0` | Number of times the droplet has cycled through the lifecycle. |
| `last_recalled` | `datetime \| None` | `None` | When the droplet was last recalled. |
| `last_transformed` | `datetime \| None` | `None` | When the droplet last changed phase / content. |
| `last_verified` | `datetime \| None` | `None` | When the droplet was last verified. |

---

## Serialization

Every component (`State`, `Permissions`, `Links`, `Cycle`, `Droplet`) implements
`to_dict()` and a classmethod `from_dict()`.

### `to_dict()`

`Droplet.to_dict(include_embedding=False)` produces a JSON-ready dict. Notes:

- Timestamps are emitted as ISO-8601 strings in UTC; `None` stays `None`.
- Enums are emitted as their string values (`phase`, `reservoir`, `visibility`,
  `retention`).
- `meta` is included **only if non-empty**.
- `embedding` is included **only if** `include_embedding=True` and it is set.

### `from_dict()` — alias-tolerant ingest

`Droplet.from_dict()` accepts the canonical shape and is deliberately tolerant of
the PRD's own example blobs (§5.2, §6, §12, §10.1) so they round-trip without data
loss. The accepted aliases and fallbacks:

| Incoming key(s) | Mapped to | Notes |
| --- | --- | --- |
| `memory_id` | `id` | `id` wins if both present; otherwise a new `mem_<hex>` id is generated. |
| `type` | `memory_type` | `memory_type` wins if both present. |
| `tags`, or a list-valued `context` | `semantic_tags` | Precedence: `semantic_tags` → `tags` → list `context`. |
| `charge` | `state.emotional_charge` | Handled by `State.from_dict` `_STATE_ALIASES`. |
| top-level state floats (e.g. `temperature`, `pressure`, `charge`) | `state.*` | If a state float appears at the top level (§12 Example A) it is folded into `state`, without overriding an explicit nested `state` value. |
| `scope` | `permissions.owner` + `permissions.visibility` | `user_private` → owner `user`, visibility `private`; a bare visibility value (`private`/`shared`/`public`) sets visibility; anything else falls back to `private`. |
| `agent_access` | `permissions.allowed_agents` | §5.2 alias. |
| dict-valued `context` | `meta["context"]` | Preserved so recall's contextual-fit can read `context.topic` / `context.session_type`. |
| any other unrecognized top-level key | `meta[key]` | e.g. the §10.1 `reason`, `usable_for_generation`, `requires_filtering` blob keys are preserved verbatim. |

Reservoir values are normalized through `normalize_reservoir` (so spec display
names like `surface_reservoir` are accepted). Unknown phases or visibility/retention
values raise (they must be valid enum members).

#### How the spec blobs map

- **§5.2 droplet** (`memory_id`, `type`, `scope: user_private`, `agent_access`,
  `emotional_charge`, `requires_consent_for_external_use`) →
  `id`, `memory_type`, `owner`/`visibility`, `allowed_agents`,
  `state.emotional_charge`, `permissions.requires_consent_for_external_use`.
- **§12 Example A** (top-level `charge`, `pressure`, `phase`; list `context`) →
  `state.emotional_charge`, `state.pressure`, `phase`, `semantic_tags`.
- **§10.1 contamination blob** (`memory_id`, `phase: polluted`,
  `reservoir: contaminated_pool`, `reason`, `usable_for_generation`,
  `requires_filtering`) → `id`, `phase`, `reservoir: contaminated`, and the three
  governance keys preserved under `meta`.

---

## Canonical JSON example

A real `Droplet.to_dict()` output (generated from the reference implementation).
Ingested from the alias-tolerant shape `{ "scope": "user_private", "agent_access":
[...], "state": { ..., "charge": 0.3 } }` and emitted in canonical form:

```json
{
  "id": "mem_a1b2c3d4",
  "content": "User prefers dark roast coffee in the morning.",
  "source": "conversation",
  "created_at": "2026-05-20T14:30:00+00:00",
  "phase": "liquid",
  "reservoir": "surface",
  "memory_type": "preference",
  "semantic_tags": [
    "coffee",
    "morning-routine"
  ],
  "state": {
    "temperature": 0.6,
    "pressure": 0.0,
    "gravity": 0.0,
    "purity": 0.9,
    "salinity": 0.0,
    "depth": 0.0,
    "fluidity": 0.0,
    "integrity": 0.0,
    "confidence": 0.8,
    "emotional_charge": 0.3
  },
  "permissions": {
    "owner": "user",
    "visibility": "private",
    "allowed_agents": [
      "recall",
      "reflection"
    ],
    "retention": "temporary",
    "external_sharing": false,
    "requires_consent_for_external_use": false,
    "requires_user_review": false
  },
  "links": {
    "associations": [
      "mem_99887766"
    ],
    "contradictions": [],
    "supports": [],
    "derived_from": []
  },
  "cycle": {
    "cycle_count": 2,
    "last_recalled": "2026-05-24T09:00:00+00:00",
    "last_transformed": null,
    "last_verified": null
  }
}
```

> `meta` and `embedding` are absent here: `meta` was empty, and `embedding` is only
> emitted when `to_dict(include_embedding=True)` is called with a vector set.
