# Research note: memory as an interacting network (query-conditioned recall over links)

- **Status:** **Active — Phase 1 built (2026-05-25).** The spreading-activation spine is
  implemented in `hydromemory/activation.py` and wired into `precipitate` (opt-in
  `RecallWeights.activation_bonus`); see [ADR-0030](../adr/0030-query-conditioned-spreading-activation.md)
  and the [gap-closure design](../closing-the-gaps.md). The autonomic-consolidation,
  decay, packing, and ANN follow-ons are documented as ADRs 0031–0034 (phased). The
  **composition / reader step** (§5 below) is now built too: an LLM reader over the activated
  constellation with citations — `hydromemory/reader.py` + `Engine.answer`,
  [ADR-0035](../adr/0035-reader-over-activated-constellation.md).
- **Date:** 2026-05-25 (originated); promoted to active 2026-05-25.
- **Scope:** Net-new. This is **out of scope for both v1 and v2** and is *not* one of
  their documented deferrals (ANN search, external broker, key rotation, grant UI).
  It is a newly surfaced idea about the recall model itself, kept separate from the
  v1/v2 backlog on purpose.

This note exists so we can dissect the idea later. It captures the question that
triggered it and the conclusion we reached — nothing here is a committed design.

## 1. The triggering question

Framed via a concrete scenario: a lawyer, a legal assistant, or an ordinary person
asks a question about some law, against a company knowledge base / corpus.

The key reframe (the user's, and it's the important part): **a corpus is just
memory.** Each law (or clause) is a memory. The correct answer to an inquiry rarely
lives in a single memory — it depends on *another memory, or a group of memories,*
interacting to satisfy the question. A clause means nothing without its definitions,
its exceptions, and the amendment that superseded it.

The metaphor that crystallized it: **water interacting with a group of trees through
the roots.** The question is water entering the system; the connections between
memories are the root network; what gets drawn up depends on *where the water enters
and how it flows through the connections that happen to be there.* The same corpus
answers two different questions differently because the water takes a different path
through the roots. The memory **behaves as needed, determined by the external entity**
(the question / the asker / the context).

## 2. The conclusion we reached

**At the protocol level, this fits HydroMemory cleanly — and the earlier
"document-QA vs. memory" split was too hard a line.** A corpus is memory; a law is a
droplet (the schema is content-agnostic; `factual` / `procedural` memory types already
exist). The legal KB is a legitimate instance of the protocol, not a category error.

The substance of the insight:

- **The answer lives in the *relationships* between memories, not in any single
  droplet.** That interdependence is the `links` graph (definitions, exceptions,
  `supersedes`/`contradicts`/`references`).
- **The water-through-roots picture is spreading activation over that graph,
  conditioned by the question.** Query = water entering at the matched droplets;
  links = roots; activation propagates through the connected subgraph; the surfaced
  answer is the *constellation* that activates together — and which constellation
  activates is determined by the external entity (the question).
- **This is literally the protocol's own thesis:** *"memory is information moving
  through state, and the state is driven by context."* The recall *modes*
  (literal / pattern / warning / reflective) are already a small instance of "the
  memory behaves differently depending on who's asking and why." The idea here just
  generalizes that from *how one droplet surfaces* to *which group activates and how
  they combine.*

## 3. Where the reference implementation fell short (pre-spine — closed in Phase 1)

> **Closed (2026-05-25):** Phase 1 implemented exactly this — `spread_activation` in
> `hydromemory/activation.py` now traverses `links`, and `precipitate(traverse=True)`
> expands and scores the activated constellation. The text below is the *pre-spine*
> state that motivated the build; it is retained as the rationale.

The gap was **implementation, not concept.** The reference impl drew the roots but
did not let water flow through them:

- `hydro_recall_score` (`hydromemory/recall.py`) scores each droplet **in isolation**
  and **never traverses a link**. Links live in the schema and only flip the recall
  *mode* to `WARNING` on a contradiction (`select_recall_mode`) — they do not
  participate in scoring or expansion.
- There is **no spreading activation**, no "this memory pulls in the group it depends
  on," and no composition of several droplets to satisfy one inquiry.
- The context-conditioning that *does* exist (`contextual_fit`, `trigger_similarity`)
  is shallow tag/topic/session matching — not the question-driven flow described
  above.

So the most ambitious third of the protocol — **memory as an *interacting network***
— is the unbuilt frontier.

## 4. Why this would be "finishing the protocol," not grafting RAG on

The protocol already gives us the vocabulary to parameterize the flow. The droplet
state vector is *hydraulic*:

- `fluidity` — how readily activation conducts *out* of a node (edge conductance).
- `pressure` / `gravity` — how hard a memory pulls / its head in the network.
- `depth` — resistance to surfacing.
- `purity` / `salinity` — whether a memory should *contaminate* the answer it flows
  into (epistemic hygiene as the water mixes).

These are not just per-droplet recall knobs; they are the natural parameters for **how
activation propagates through the root network and how much each memory contributes to
what the external entity draws up.** Realizing the legal-KB case is therefore
*finishing what the protocol started* — turning recall from "score isolated droplets"
into "let the question flow through the connected network and see what surfaces
together."

## 5. Open questions for later dissection

- **Corpus → links:** how do links get populated from a real corpus (legal-aware
  parsing of cross-references, defined terms, "subject to §X", supersession)? Manual,
  learned, or LLM-extracted?
- **The flow algorithm:** a concrete spreading-activation / graph-walk over `links`,
  with hop decay parameterized by the hydraulic state vector (`fluidity` as
  conductance, `depth` as resistance, `purity`/`salinity` as mixing/contamination).
- **Query-conditioned edge selection:** the external entity should pick *which* edge
  types to follow (a question about exceptions follows `exception_to`; about meaning
  follows `defines`; about currency follows `supersedes`). How is intent mapped to
  edge selection?
- **Composition / reader step — BUILT (ADR-0035).** `hydromemory/reader.py`
  (`compose_answer`) + `Engine.answer` compose an answer over the activated constellation
  with `[n]`→droplet-id citations; the composer is pluggable (offline extractive default /
  Claude LLM composer). *Remaining here:* an entailment/grounding check that the answer is
  actually supported by the cited droplets (citations are currently clamped-to-range, not
  verified-as-entailed).
- **Relation to existing pieces:** how this rides on top of `search_similar`
  (entry-point retrieval) + `links` (the graph) + recall modes (behavior) without
  rewriting the §5.6 score.
- **Evaluation:** how do we measure "the right constellation surfaced for *this*
  question" — beyond single-passage precision?

## 6. Reminder

This is parked deliberately so it does not derail the v1/v2 deferred backlog. Pick it
up as its own initiative when we choose to.
