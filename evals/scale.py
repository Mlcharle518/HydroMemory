"""Scale eval (#1, scale half): faiss ANN vs brute-force exact — the approximation
cost (recall@k) and the query latency as the corpus grows. (evals/README.md §3.)

Operates on the vector index directly with random vectors — no embeddings or engine,
so the embedding `backend` arg is ignored. Requires faiss-cpu; emits a single
"not installed" note otherwise (brute-force is the default elsewhere).
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
import time
from typing import Any

import numpy as np

from evals.harness import EvalResult
from evals.metrics import mean
from hydromemory.storage.vector_index import build_vector_index

_HAS_FAISS = importlib.util.find_spec("faiss") is not None


def _build(backend: str, path: str, dim: int, vectors: np.ndarray) -> Any:
    # rebuild() populates in one shot — avoids VectorIndex.add's per-insert vstack
    # (which is O(N^2) to build a brute index).
    index = build_vector_index(path, dim, backend=backend)
    index.rebuild([(f"v{i}", vectors[i]) for i in range(len(vectors))])
    return index


def run(
    *,
    backend: str = "local",  # ignored: scale tests the index, not the embedder
    dim: int = 64,
    n_values: tuple[int, ...] = (1000, 5000, 20000),
    k: int = 10,
    queries: int = 50,
    seed: int = 0,
    **_: Any,
) -> list[EvalResult]:
    if not _HAS_FAISS:
        return [
            EvalResult("scale", "faiss", "available", 0.0, 0,
                       {"note": "faiss-cpu not installed; scale requires it (pip install faiss-cpu)"})
        ]
    rng = np.random.default_rng(seed)
    tmp = tempfile.mkdtemp(prefix="hydroeval-scale-")
    results: list[EvalResult] = []
    for n in n_values:
        vectors = rng.standard_normal((n, dim)).astype(np.float32)
        probes = rng.standard_normal((queries, dim)).astype(np.float32)
        brute = _build("brute", os.path.join(tmp, f"b{n}.npz"), dim, vectors)
        ann = _build("faiss", os.path.join(tmp, f"f{n}.npz"), dim, vectors)

        recalls: list[float] = []
        brute_ms: list[float] = []
        faiss_ms: list[float] = []
        for q in probes:
            start = time.perf_counter()
            exact = [i for i, _s in brute.search(q, k)]
            brute_ms.append((time.perf_counter() - start) * 1000.0)
            start = time.perf_counter()
            approx = [i for i, _s in ann.search(q, k)]
            faiss_ms.append((time.perf_counter() - start) * 1000.0)
            recalls.append(len(set(exact) & set(approx)) / k)

        detail = {"n": n, "dim": dim, "k": k, "queries": queries}
        results.append(EvalResult("scale", "brute", f"recall@{k}:N={n}", 1.0, queries, detail))
        results.append(EvalResult("scale", "faiss", f"recall@{k}:N={n}", round(mean(recalls), 4), queries, detail))
        results.append(EvalResult("scale", "brute", f"latency_ms:N={n}", round(mean(brute_ms), 4), queries, detail))
        results.append(EvalResult("scale", "faiss", f"latency_ms:N={n}", round(mean(faiss_ms), 4), queries, detail))
    return results
