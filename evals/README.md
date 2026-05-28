# HydroMemory efficacy evals — design

> Status: **Design + Phases 1–2 complete (synthetic, no-LLM evals).** On
> local MiniLM: `multihop` (#2) lifts supporting-fact recall **0.67 → 1.00** (traverse
> off → on, flat precision); `pollution` (#3) drives stale-resurfacing **1.00 → 0.00**
> while rare-true retention holds at **1.00** (the stale-vs-rare invariant — a flat store
> resurfaces 100%); `consolidation` (#4) makes the distilled principle the **#1 result in
> 4/4 themes** (compresses 4 → 1) — *recallable by an ordinary approved agent after moving principles SACRED → CLOUD* (ADR-0036;
> §7); `scale` (#1) shows faiss at **~6.6× lower query latency** than brute at N=20k for **recall@10 ≈ 0.95**. Phase 3 (LLM-in-loop) is started — `packing` + `multihop_qa`
> (an LLM reader) are built and run on an API key. First runs (opus-4-7): `packing` shows
> **no lost-in-the-middle** even at a ~150-item haystack (flat 1.00 across all needle
> positions — placement unvalidated for strong models at this scale); `multihop_qa` (via the
> promoted reader, ADR-0035) comes out ≈ tied at k=10 — the 15-droplet corpus is too small for
> retrieval to be the bottleneck, so the toy QA eval can't isolate the traversal benefit (§7). This document is the source of truth for the `evals/` harness: it
> freezes the harness contract (§4), the synthetic dataset formats (§5), and the phased
> plan (§6). It is the efficacy counterpart to
> [docs/closing-the-gaps.md](../docs/closing-the-gaps.md) — that doc built the
> *mechanisms*; this one measures whether they *help*.

## 1. Why this exists

The 506-test suite and [tests/test_success_metrics.py](../tests/test_success_metrics.py)
(the PRD §16 metrics) are **correctness** tests: they prove each mechanism does what we
coded it to do, on hand-picked single cases (e.g. "the architecture memory ranks above
the cooking one"). They do **not** measure efficacy — whether spreading activation
actually answers multi-hop questions better than plain RAG, whether the forgetting model
actually keeps stale facts down on a realistic stream.

> **Everything we've tested proves the machinery runs. Nothing yet measures whether it
> *helps*.** This harness closes that gap with metrics, baselines, and a distribution of
> cases instead of green checkmarks.

## 2. Principles

1. **Always against a baseline.** "Good at multi-hop" is meaningless without "...vs.
   what." Most baselines are built in and need no extra code: `traverse=False` is the
   RAG baseline; a plain store with no contamination-routing / decay is the flat-store
   baseline; no-consolidation and unpacked-concat are the others. We A/B against
   ourselves.
2. **A distribution, not one case.** Every eval runs tens–hundreds of cases and reports a
   *number* (recall@k, resurfacing rate), with a per-case breakdown.
3. **The right metric per problem** (§3) — retrieval quality, resurfacing rate,
   compression, positional accuracy, latency.
4. **Real embeddings, not the stub.** Evals run on the `local` MiniLM backend
   (`embedding_backend=local`), never the hash stub — otherwise "semantic" recall is
   gameable and the numbers are meaningless. (torch is already installed.)
5. **Out of CI.** Evals emit *metrics/reports*, not pass/fail gates; some need a real LLM
   ($ + non-deterministic). They live in `evals/`, run on demand (`python -m evals.run`),
   and never join the deterministic `tests/` suite. (Regression thresholds can be layered
   on later.)
6. **Don't grade our own homework.** Hand-built synthetic data (the agreed starting point)
   is a *controlled demonstration of a mechanism's effect*, not proof on natural data. The
   credibility milestone is an **external** benchmark (§6, Milestone).

## 3. The four problems → eval design

| # | Eval | Data | Baseline → Treatment | Metric | LLM? |
|---|------|------|----------------------|--------|------|
| 2 | `multihop` | chained-fact corpus + questions (synthetic) | `precipitate(traverse=False)` → `traverse=True, activation_bonus>0` | **supporting-fact recall@k** (+ lift) | retrieval: no |
| 3 | `pollution` | event stream with later-corrected + rare-true facts | flat store (no routing/decay) → full lifecycle (pollute/filter + decay) | **stale-resurfacing rate** ↓, **rare-true retention** ↑ | no |
| 4 | `consolidation` | clusters of related episodes + matching queries | raw episodes → `Mesh(consolidate=True)` then recall | **compression ratio**, **principle sufficiency** | partial |
| 1 | `packing` | needle-in-haystack by position | unpacked concat → `pack_working_set` placement | **answer accuracy vs needle position** | **yes** |
| 1 | `scale` | synthetic vectors at growing N | brute-force → faiss | **recall@k vs brute** + **query latency vs N** | no |

Notes per eval:

- **`multihop`** measures *retrieval* (did the right constellation surface?), **not**
  answer accuracy — there is no LLM reader over the activated subgraph yet (parked in the
  research note). End-to-end QA accuracy is a Phase 3 add once a reader exists. The
  synthetic corpus is built so the gold constellation is reachable via `links` but **not**
  all top-k by cosine — that gap is precisely what `traverse` is supposed to close.
- **`pollution`** is the strongest differentiator: a flat store keeps surfacing a
  corrected fact; HydroMemory should drive stale-resurfacing toward 0 (routing +
  `purity`↓ + decay) **while** keeping rare-true retention high (the stale-vs-rare
  invariant from ADR-0032). The eval must report *both* — driving resurfacing to 0 by
  over-forgetting everything is a failure.
- **`consolidation`** measures compression (N episodes → 1 principle that still satisfies
  the query at threshold) without an LLM; the harder *re-derivation cost* needs an agent
  loop and is Phase 3.
- **`packing`** only matters with a real model (placement can't help a model that isn't
  there), so it's LLM-gated; pins model + seed and reports accuracy by needle position.
- **`scale`** uses faiss (installed) vs brute-force ground truth: reports both the recall
  cost of approximation *and* the latency win as N grows.

## 4. The harness contract (FROZEN)

This section is the interface contract the implementation conforms to.

```
evals/
  README.md            # this design (source of truth)
  __init__.py
  data/                # hand-built synthetic datasets (JSON, version-controlled)
    multihop.json
    pollution.json
  datasets.py          # load + validate dataset JSON -> typed records
  metrics.py           # pure metric functions (no I/O)
  harness.py           # EvalResult, build_eval_engine(...), ingest_corpus(...)
  multihop.py          # Phase 1 eval
  pollution.py         # Phase 1 eval
  run.py               # CLI: python -m evals.run [--eval NAME] [--backend local|stub] [--json]
  results/             # emitted reports (gitignored)
```

### 4.1 Result + runner

```python
@dataclass(frozen=True)
class EvalResult:
    eval: str            # "multihop"
    condition: str       # "baseline" | "treatment"
    metric: str          # "support_recall@10"
    value: float
    n: int               # number of cases aggregated
    detail: dict         # per-case rows, config, backend, seed, model

def run_eval(name: str, *, backend: str = "local", **opts) -> list[EvalResult]:
    """Run one eval end to end; return one EvalResult per (condition, metric)."""
```

Each eval module exposes `run(backend, **opts) -> list[EvalResult]`. `run.py` dispatches,
prints a comparison table (baseline vs treatment + the lift), and writes JSON to
`evals/results/{eval}-{timestamp}.json`.

### 4.2 Engine builder + ingestion (reused across evals)

```python
def build_eval_engine(*, backend="local", vector_backend="brute", vault=False, seed=0):
    """A real Engine on local embeddings (NOT the stub) for realistic recall.
    vault=False -> plain store (the flat-store baseline path)."""

def ingest_corpus(engine, corpus: list[CorpusDroplet]) -> None:
    """Absorb each corpus droplet and wire its declared links (Verbs.flow)."""
```

### 4.3 Metrics (pure)

```python
def recall_at_k(retrieved_ids: Sequence[str], gold_ids: set[str], k: int) -> float
def precision_at_k(retrieved_ids: Sequence[str], gold_ids: set[str], k: int) -> float
def mrr(retrieved_ids: Sequence[str], gold_ids: set[str]) -> float
def resurfacing_rate(checkpoints: Sequence[CheckpointOutcome]) -> float   # stale present / total
def rare_true_retention(probes: Sequence[ProbeOutcome]) -> float           # rare-true recalled / total
def compression_ratio(episodes: int, principles: int) -> float
```

## 5. Synthetic datasets (hand-built first)

Phase 1 ships **hand-built** JSON datasets, by deliberate choice — they let us demonstrate
each mechanism's effect with full control before taking on the cost of an external
benchmark. **Honesty caveat (§2.6):** because we author the data, these results are a
*controlled demonstration*, not evidence on a natural distribution; the external benchmark
(§6) is what turns "demonstrates the effect" into "is actually competitive."

### 5.1 `multihop.json`

```json
{
  "corpus": [
    {"id": "f1", "content": "The Aurora API authenticates with rotating JWTs.",
     "links": {"associations": ["f2"]}},
    {"id": "f2", "content": "Rotating JWTs are issued by the Keymint service.",
     "links": {"associations": ["f3"]}},
    {"id": "f3", "content": "Keymint stores its signing keys in the Vault HSM."}
  ],
  "questions": [
    {"id": "q1", "seed": "how does the Aurora API authenticate",
     "gold_support": ["f1", "f2", "f3"], "answer": "via JWTs from Keymint, keyed in the Vault HSM"}
  ]
}
```

The `seed` is written to be cosine-near **f1 only**; `f2`/`f3` are reachable through
`links` but not directly similar — so `traverse=False` recovers ~1/3 of `gold_support`
and `traverse=True` should recover most of it. `answer` is unused until a reader exists.

### 5.2 `pollution.json`

A timeline of operations + checkpoints:

```json
{
  "timeline": [
    {"op": "absorb", "id": "p1", "content": "The launch date is March 3."},
    {"op": "absorb", "id": "r1", "content": "The CEO's daughter is named Mei.", "rare_true": true},
    {"op": "correct", "target": "p1", "with": "The launch slipped to April 10.", "new_id": "p1b"},
    {"op": "checkpoint", "probe": "when is the launch", "expect_absent": ["p1"], "expect_present": ["p1b"]},
    {"op": "decay", "cycles": 30},
    {"op": "checkpoint", "probe": "what is the CEO's daughter named", "expect_present": ["r1"]}
  ]
}
```

`correct` marks the old fact (`pollute` + a `contradictions` link, then `filter` of the
replacement); `checkpoint` runs recall on `probe` and scores resurfacing (stale in results)
and rare-true retention. `rare_true` facts are low-salience and never contradicted — they
must survive decay.

## 6. Roadmap

**Now — Phase 1 (synthetic, no LLM): both built.** `multihop` (#2) — support-fact recall
**0.67 → 1.00** (lift +0.33, flat precision). `pollution` (#3) — stale-resurfacing
**1.00 → 0.00** while rare-true retention holds at **1.00** (the stale-vs-rare invariant;
a flat store resurfaces 100%). Both on local MiniLM, baselines built in
(`traverse=False`, flat store). The crisp numbers reflect clean synthetic data with an
explicit correction signal — they test the *mechanism's* effect, not staleness
auto-detection (backend-bound).

**Phase 2 (no LLM) — complete.** `consolidation` — the distilled principle is the **#1
result in 4/4 themes** and compresses **4 → 1**, scored under an **ordinary approved agent**
(no user-proxy) now that principles land in CLOUD rather than SACRED — see the finding in §7
and ADR-0036. `scale` — faiss vs brute on random vectors: at **N=20k, ~6.6× lower
query latency** (1.28 → 0.20 ms) at **recall@10 ≈ 0.95** (brute is O(N), faiss sub-linear;
the latency gap widens with N).

**HydroIntent — `intent_distillation` (no LLM) — built.** The memory→intent analog of
`consolidation` (ADRs 0037–0041): link a constellation of repeated memories, `detect_intent`
(cluster→distill) into one CANDIDATE intent, then retrieve over the intent store for each
theme's direction query. First run (opus-4-7 N/A — embeddings only, MiniLM `local`):
`intent_present_rate` **0.00 → 1.00**, `intent_top1_rate` **0.00 → 0.75** (the right intent
is #1 for 3/4 themes; the 4th is in-top-k but out-ranked by a semantically-neighboring
direction — an honest retrieval limit, not a mechanism failure), `provenance_coverage`
**1.00** (intents link all their source memories — the explainability substrate), and
`compression_ratio` **4 → 1**. Baseline = no intent layer (no directional artifact to
retrieve, 0 by construction). Run: `python -m evals.run --eval intent_distillation --backend local`.

**Phase 3 (LLM-in-the-loop, on demand) — started.** `packing` **(built)** — places a needle
at the EDGES (first/last slot) vs the MIDDLE of a filler block and compares a real model's
answer accuracy by zone, validating the primacy/recency premise behind `pack_working_set`.
LLM-gated, so run it with a key: `ANTHROPIC_API_KEY=… python -m evals.run --eval packing`
(≈3 calls/case; `HYDRO_CLAUDE_MODEL` picks the model — use a cheap one to save cost). It
skips with a note when no key is set. `multihop_qa` **(built)** — an LLM **reader** answers
each question from the recalled constellation; metric `answer_key_coverage` = the chained-fact
(2nd/3rd-hop) terms that surface in the answer, comparing `traverse=False` vs `traverse=True`,
so it tests whether traversal lifts the *answer*, not just retrieval
(`ANTHROPIC_API_KEY=… python -m evals.run --eval multihop_qa`, ≈10 calls). **Remaining:** the
consolidation *re-derivation cost*; citations + promoting the reader into hydromemory proper
(an ADR); then the external LongMemEval milestone.

**Phase 4 (external) — first real LongMemEval run done (oracle subset, Haiku).** `evals/longmemeval.py` wraps
HydroMemory as a **[LongMemEval](https://github.com/xiaowu0162/LongMemEval)**-compatible
memory+QA system: ingest a question's multi-session history as droplets → `Engine.answer`
(recall + reader) → LLM-judge by `question_type`. Proven end-to-end on a synthetic
LongMemEval-format sample (`evals/data/longmemeval_sample.json`): **4/4 across all four
question types** — a sanity proof that the integration works, **NOT a benchmark score**.
Run the **real** set with `--dataset path/to/longmemeval_s.json` (download from the
LongMemEval GitHub/Drive). The real benchmark — 500 hard questions over long, noisy,
distractor-filled histories — is the actual credibility test; it's a real compute + API-cost
commitment, so use `--limit` and a cheap `HYDRO_CLAUDE_MODEL`. (Its categories —
multi-session reasoning, *knowledge updates* = our pollution problem, temporal reasoning,
abstention — map almost 1:1 to the four problems; LoCoMo is the conversational analogue.)
Everything before this is in-house demonstration.

**First real run** — LongMemEval `oracle`, a seeded 40-question shuffled subset, Haiku
(`claude-haiku-4-5-20251001`): **overall 52.5%**. By type: knowledge-update **0.83** (its
design strength — the pollution/staleness analogue), single-session-user **1.00**,
single-session-assistant **0.75**, multi-session **0.55**, temporal-reasoning **0.21** (a real
weakness — recall + reader do no temporal reasoning over `question_date`/`haystack_dates`),
preference 0.00 (n=1, noise). Caveats: `oracle` is the **easier** setting (evidence-only, no
distractor sessions; `longmemeval_s`'s long noisy haystacks stress retrieval and would likely
score lower), on a small/cheap model, with small per-type n. But it's a real external number,
and the knowledge-update strength + temporal weakness line up with the architecture. Next:
`longmemeval_s` (the hard retrieval variant) and a stronger model.

## 7. Scope & honest limitations

- **Synthetic ≠ natural.** Phase 1 demonstrates mechanism effects on data we authored; it
  can flatter the system. Believe the *direction and magnitude of the lift*, not the
  absolute numbers, until Phase 4.
- **Retrieval ≠ QA.** `multihop` measures whether the right droplets surface, not whether
  a model answers correctly — no reader exists yet.
- **Quality is backend-bound.** Evals run on `local` embeddings; results would shift with a
  different embedder (and LLM evals with a different model). Always report the backend +
  seed + model in `EvalResult.detail`.
- **Not a CI gate.** Evals report; they don't pass/fail. A future pass may add regression
  thresholds (e.g. "multihop lift must stay ≥ X").
- **Finding (RESOLVED — ADR-0036) — consolidation recall was identity-gated.** Distilled
  principles originally landed in **SACRED**, which governance restricts, so an ordinary
  approved/session agent **could not recall them** (`principle_present_rate` measured 0.00
  under an approved agent → 1.00 under a user-proxy). That defeated ADR-0031's stated reuse
  goal: principles exist to be reused at recall *by ordinary session agents*, but SACRED is
  for *user-declared* anchors. **Resolution:** `distill` now lands principles in **CLOUD**
  (the abstraction layer, approved-agent readable; the natural top of the
  `evaporate→condense→distill` ladder), so this eval now scores **1.00 under an ordinary
  approved agent**, and SACRED is reserved for genuinely consent-gated identity memory.
  (Note: the user's exploratory "e.g. GROUNDWATER" alternative would *not* have fixed it —
  GROUNDWATER is `high_trust_agents_only`, so an approved agent is still denied; CLOUD is the
  reservoir an approved agent can actually read.) A real design tension the unit tests (which
  called `distill` in isolation, never recalling under a non-proxy identity) never exposed.
- **Finding — `packing` shows NO lost-in-the-middle, even at a long haystack (null).** The
  test buries the needle in ~150 filler items (several-K tokens) and sweeps its position
  0 → 100%: opus-4-7 answers correctly at **every** position (flat **1.00**, edge = middle).
  So a strong long-context model has no measurable center-of-context penalty at this scale,
  and the packer's primacy/recency **placement** is **unvalidated / low-value** for such
  models here. Detecting the effect would need far longer contexts (tens of thousands of
  tokens) or a weaker model. The packer's token budget / provenance-dedup /
  abstraction-preference are separate concerns and still earn their keep — only *placement*
  is in question.
- **Finding — retrieval lift ≠ answer lift, and the toy QA eval is underpowered.** Traversal
  lifts supporting-fact *recall* **+0.33** (`multihop`, k=5: 0.67 → 1.00), but end-to-end
  *answer-key coverage* (`multihop_qa`, via the promoted reader, ADR-0035) comes out **≈ tied**
  (baseline ≈ traverse ≈ 0.70 at k=10; an earlier "+0.10" was one key-check flipping — noise
  across 5 questions). The reason is methodological: at **k=10 over a 15-droplet corpus, cosine
  already retrieves most facts**, so retrieval isn't the bottleneck and traversal's benefit
  washes out. A real end-to-end QA number needs a bigger corpus + more questions with **k ≪
  corpus** (keeping retrieval the bottleneck) — which is what LongMemEval (Phase 4) provides.
  The reader *feature* works (tested); the toy QA *number* just can't isolate the benefit here.
- **`longmemeval` 4/4 is a sanity proof, NOT a benchmark score.** The adapter answers all
  four synthetic LongMemEval-format instances correctly (every question type), which proves
  the ingest → recall → reader → judge pipeline works end to end — but the cases are short and
  hand-built. The only number that counts is the **real** LongMemEval (long noisy histories,
  distractor sessions, 500 questions); run it via `--dataset`. Don't read 4/4 as "HydroMemory
  aces LongMemEval."
- This supersedes the assertion-flavored "Evaluation" sketch in
  [closing-the-gaps.md §6](../docs/closing-the-gaps.md) — that listed checks; this is the
  measurement harness.
