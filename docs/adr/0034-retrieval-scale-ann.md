# ADR-0034: retrieval scale (pluggable ANN vector index)

Status: Accepted — implemented (see ../closing-the-gaps.md §5)

> **Implemented + validated 2026-05-25.** The seam is `VectorIndexProtocol` +
> `build_vector_index(path, dim, backend='brute'|'hnswlib'|'faiss'|'ann')`
> (`storage/vector_index.py`); `SqliteDropletRepository` builds its index through it
> from `config.vector_backend` (`HYDRO_VECTOR_BACKEND`, default `brute`). Two backends
> in `storage/ann_index.py` behind the same `add`/`remove`/`search`/`rebuild`/`persist`/
> `load` + `(id, cosine)` contract: `AnnVectorIndex` (hnswlib) and `FaissVectorIndex`
> (faiss HNSW, inner-product on normalized vectors = cosine, with soft-delete + filter
> since faiss HNSW has no native delete); `'ann'` picks the best installed. Both libs
> are lazy-imported and optional; brute-force stays the exact default.
> **Validated locally with faiss-cpu** (`tests/test_ann_index.py`): recall@k parity vs
> brute-force AND a remove/replace/persist round-trip pass for real (suite 497→506, 2
> skips). hnswlib needs a C++ toolchain (MSVC on Windows), so that backend stays
> wired-but-unexercised here — its parity runs once hnswlib is installed. Documented
> approximations: recall is approximate (parity-guarded); an `allowed_ids`-filtered
> query oversamples then post-filters (best-effort).

## Context

Per [ADR-0012](0012-sqlite-plus-vector-index-storage.md) the `VectorIndex`
(`hydromemory/storage/vector_index.py`) is a file-backed **brute-force exact
cosine scan**: numpy, L2-normalized rows, one matrix-vector dot product
(`self._matrix @ q`), persisted next to the database as `{db_path}.vec.npz`. That
was a deliberate correctness/determinism choice — exact results, stable ordering,
no external services — and it is the right default for the suite and small
corpora.

But `search` is O(N) in the corpus size per query, so it does not meet the
*scale/latency* half of problem #1 (the "year of conversations" case in
[closing-the-gaps.md](../closing-the-gaps.md) §3, row #1). Spreading-activation
traversal ([ADR-0030](0030-query-conditioned-spreading-activation.md)) seeds from
the `search_similar` top-k and then *widens* the working set; it makes a fast,
high-quality entry set matter **more**, not less.

## Decision

Introduce a **pluggable Approximate-Nearest-Neighbour backend behind the existing
index contract**. The `VectorIndex.search` / `DropletRepository.search_similar`
signatures and the `(id, cosine)` result shape are **unchanged**, so
`pipeline`/`recall`/`Verbs` and the traversal seeding in §4.5 are untouched — an
ANN backend is a drop-in alternative `search`/`add`/`remove`/`rebuild`
implementation, selected at construction.

- An ANN library (e.g. `hnswlib` / `faiss`) ships as an **optional heavy extra**,
  exactly like the `local` embeddings extra in
  [ADR-0026](0026-real-model-backends.md) — never a core dependency. `numpy`
  stays the only required runtime dep ([ADR-0012](0012-sqlite-plus-vector-index-storage.md)).
- The **brute-force exact index remains the default** — used for CI, tests,
  determinism, and small corpora. ANN is opted into by config or a corpus-size
  threshold (small corpora keep the exact scan, where it is already fast and
  precise).
- Ship a **recall@k parity test** asserting the ANN backend agrees with
  brute-force on a fixture corpus within tolerance (the #1 scale check named in
  [closing-the-gaps.md](../closing-the-gaps.md) §6).
- Keep the `.vec.npz`-style **persistence/rebuild** story working: the index stays
  a rebuildable cache (`rebuild_index`), recoverable on a dim mismatch.

## Consequences

- Closes the scale/latency half of #1: sub-linear entry retrieval at the
  "year of conversations" scale, with no change to the engine contract.
- **Entry retrieval only.** Traversal (ADR-0030), the §5.6 recall score,
  consolidation (ADR-0031), and governance are unchanged — ANN swaps the candidate
  *source*, not the candidate *handling*.
- Approximate recall is a documented accuracy/latency tradeoff, **guarded by the
  parity test** so drift against exact cosine is caught.
- The ANN dependency is an optional heavy extra, kept out of default CI like torch
  in [ADR-0026](0026-real-model-backends.md); the brute-force default preserves
  determinism and the green all-default suite ([ADR-0025](0025-additive-layering-v1-stays-green.md)).
- Index build/update has a cost: some ANN structures (e.g. HNSW) are
  insert/append-friendly and fit `add`/`remove`, while others are batch-built and
  need a periodic `rebuild` — the cache/rebuild contract already covers this.
