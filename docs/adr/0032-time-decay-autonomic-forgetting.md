# ADR-0032: time decay + autonomic forgetting

Status: Accepted — implemented (see ../closing-the-gaps.md §5)

> **Implemented 2026-05-25.** `forgetting.decay` fades *salience only*
> (`pressure`/`fluidity`/`temperature`) by `salience_factor ** idle_cycles` and
> never touches `purity`/`integrity`/`confidence` — the invariant that keeps
> rare-but-true distinct from stale-false; at a floor it records a
> `drain`/`sediment` *demote suggestion* in `meta` (never `delete`).
> `forgetting.select_aged(repo, ...)` replaces the passthrough with a real store
> query (never-verified, or `last_verified` older than `max_age`), and
> `MeshEngine` gains an optional **read-only** `repo` so `aged_droplets` runs it
> (else the empty passthrough, unchanged) plus a `decay` surface. All default-off
> (ADR-0025); tests `tests/test_decay.py`; suite 481→491. **Note:** the periodic
> *driver* — a scheduled maintain pass that selects→decays→persists demotions on a
> cadence — is external (the repo has no scheduler); this ADR ships the building
> blocks + the wiring such a pass (a maintain tick, or an external scheduler) runs through.

## Context

Forgetting is HydroMemory's strongest fit among the four problems (`../closing-the-gaps.md`
row #3). The seven graded modes in `forgetting.py` — `evaporate`/`drain`/`sediment`/
`dissolve`/`seal`/`compost`/`delete` — let a memory fade, sink, or end by degree instead of
being hard-erased, and contamination is *structurally* suppressed: a flagged droplet routes to
the CONTAMINATED reservoir with `purity`↓, `phase_accessibility` `0.0`, and a `0.95` recall
threshold (`recall.py`). That is exactly what a flat vector DB cannot do, and it is what
separates **stale-false** (low `purity`, contaminated) from **rare-true** (low salience, high
`purity`, not contaminated).

But every one of those modes is **verb-driven**: a human or an agent must *invoke* it. There is
no autonomic loop. `MeshEngine.aged_droplets` (`platform/runtime.py` ~line 139) is a passthrough
— it returns `context['droplets']` or `[]`, never running a real "find stale memory" query — and
there is **no time-based decay anywhere**. So a stale-but-not-contaminated memory keeps its
salience (`pressure`/`fluidity`/`temperature`) indefinitely and can resurface on a weak pull
until someone manually drains it. This is the open half of gap #3: detection accuracy is
backend-bound (accepted), but the *selection of what is stale* and the *passive fading of the
inactive* do not exist.

## Decision

Add **per-cycle / elapsed-time decay of the salience dimensions only**, plus a real
`aged_droplets` query, run on the mesh tick — all config-gated and default-off.

1. **Decay salience, never truth.** A decay pass nudges `pressure`, `fluidity`, and
   `temperature` toward `0` as a function of cycles elapsed since `cycle.last_recalled` /
   `cycle.last_verified` (and/or low `cycle.cycle_count`). **CRITICAL INVARIANT: decay MUST NOT
   touch `purity`, `integrity`, or `confidence`.** `purity` encodes epistemic truth; salience
   encodes recency/activeness. Decaying salience-only is precisely what preserves the
   stale-vs-rare distinction: a rare-but-true memory goes quiet (low `pressure`/`fluidity`, so a
   small `hydro_recall_score`) yet stays recallable on a *strong* pull because its `purity` is
   intact and it is **not** routed to CONTAMINATED — while a stale-*false* memory travels the
   separate contamination path (low `purity`, `WARNING` mode). Decay moves salience; it never
   manufactures contamination.

2. **A real `aged_droplets` store query.** Replace the passthrough with a store-backed selection
   of under-verified / inactive droplets — ranked by `cycle.last_verified` age and/or low
   `cycle.cycle_count` / elapsed time — feeding the Reflection role's existing `reverify`
   (`platform/runtime.py` ~line 116), which re-runs the detector and stamps
   `cycle.last_verified`. Staleness *selection* becomes real; staleness *detection* on reverify
   stays the detector's call (backend-bound, as today).

3. **Decay-to-floor emits a demote SUGGESTION, never `delete`.** When a droplet decays to a
   salience floor, the pass emits a `drain` or `sediment` *suggestion* (lose active influence /
   sink to `groundwater`) — it does not auto-`delete`. Per §11, forgetting fades influence; it
   does not destroy data without a user command. `delete` stays user-only.

4. **Opt-in, documented defaults, cascade-safe.** Decay rates / cadence / the staleness age
   cut-off are documented `PhaseConfig`-style defaults (like the `recall.py` threshold tables),
   and the whole pass is **default-off** so the standard path is byte-identical (ADR-0025). When
   enabled, it runs on the mesh tick inside the ADR-0024 cascade-safety envelope (depth guard,
   per-cycle dedupe, no-op suppression, terminal-phase guard), so a decay-driven demotion cannot
   storm.

## Consequences

- Closes the forgetting-autonomy half of gap #3: inactive memory now fades and stale candidates
  are actually *found*, without waiting on a human verb.
- The stale-vs-rare separation is preserved **structurally, not by tuning**: rare-true stays
  high-`purity` and recallable; stale-false stays the contamination path. The invariant is the
  guarantee — touch `purity`/`integrity`/`confidence` and it breaks.
- **No data loss**: decay demotes (`drain`/`sediment` suggestion), it never deletes. A
  demoted droplet remains in the store, recoverable on a strong enough pull.
- Decay cadence and the staleness cut-off are documented config defaults; `aged_droplets` gives
  the Reflection role real input where it had none.
- **Default-off keeps the suite green** (ADR-0025): all-default config means no decay, the
  passthrough-equivalent empty selection, and the 461 tests are unaffected.
- Honest limits: staleness *detection* on `reverify` is still backend-bound (the contamination
  detector's `assess` call — stub vs. local/Claude, per ADR-0026); and "how aged is aged" is a
  policy **knob** in this phase, not a learned signal. We are automating *when* forgetting fires,
  not making it smarter than the backend behind it.
