# ADR-0012: Storage = SQLite + file-backed brute-force cosine vector index

Status: Accepted

## Context

The repository must persist droplets with their queryable dimensions (§7), the
droplet graph (§5.2 links), and embeddings for §5.6 semantic similarity. A
reference implementation should be easy to run anywhere (no external services),
exact, and deterministic. Pulling in a vector database or an ANN library would
add heavy dependencies and approximate, nondeterministic results.

## Decision

Back the `DropletRepository` with **SQLite plus a file-backed brute-force cosine
vector index** (numpy). The hybrid SQLite schema stores the indexed query
dimensions as columns and the rest as JSON, with a separate `links` table as the
source of truth for the graph. Embeddings power a `VectorIndex` that L2-normalizes
vectors on insert (so cosine is one matrix-vector dot product) and is persisted
next to the database as `{db_path}.vec.npz`. The index is a **rebuildable cache**:
it can always be regenerated from the embeddings stored on the rows
(`rebuild_index`).

## Consequences

- No external services; the only runtime dependency is `numpy`. Reopening a store
  recovers both rows and embeddings.
- Search is exact and deterministic — ideal for a reference impl and for stable
  tests — at the cost of linear-scan scaling. The docstrings state this trade-off
  explicitly (correctness/determinism over scale).
- Because the index is a cache, corruption or dimension changes are recoverable
  by rebuilding from the canonical rows.
- A production deployment can replace the index (or the whole repository) without
  touching the engine, which depends only on the abstract contract.
