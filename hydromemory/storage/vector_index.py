"""File-backed brute-force cosine vector index (numpy, reference scale).

The index is a *cache* over the embeddings stored on each droplet (PRD §5.6
``semantic_similarity``): it can always be rebuilt from the repository. Vectors
are L2-normalized on insert, so cosine similarity is a single matrix-vector dot
product. This is intentionally exact and simple (no ANN structures) — the
reference implementation favors correctness and determinism over scale.

Persistence: the ids + matrix are written next to the database as an ``.npz``
archive (``f"{db_path}.vec.npz"``). Empty indexes and zero vectors are handled
gracefully (a zero vector has cosine 0 against everything).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)


def _normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalize a 1-D vector; a zero vector is returned unchanged (all zeros)."""
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return vec
    return vec / norm


class VectorIndex:
    """Brute-force cosine index persisted to ``path`` as a numpy ``.npz`` file."""

    def __init__(self, path: str, dim: int) -> None:
        self.path = path
        self.dim = int(dim)
        self._ids: list[str] = []
        self._pos: dict[str, int] = {}
        # Matrix of L2-normalized rows, shape (n, dim).
        self._matrix: np.ndarray = np.zeros((0, self.dim), dtype=np.float64)

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, id: str, vector: object) -> None:
        """Insert or replace the row for ``id`` with an L2-normalized ``vector``."""
        row = _normalize(np.asarray(vector, dtype=np.float64).reshape(-1))
        if row.shape[0] != self.dim:
            raise ValueError(f"vector dim {row.shape[0]} != index dim {self.dim}")
        existing = self._pos.get(id)
        if existing is not None:
            self._matrix[existing] = row
            return
        self._pos[id] = len(self._ids)
        self._ids.append(id)
        if self._matrix.shape[0] == 0:
            self._matrix = row.reshape(1, self.dim)
        else:
            self._matrix = np.vstack([self._matrix, row])

    def remove(self, id: str) -> None:
        """Remove ``id`` if present (no-op otherwise), keeping positions consistent."""
        pos = self._pos.pop(id, None)
        if pos is None:
            return
        self._ids.pop(pos)
        self._matrix = np.delete(self._matrix, pos, axis=0)
        # Reindex positions after the removed row.
        for i in range(pos, len(self._ids)):
            self._pos[self._ids[i]] = i

    def search(
        self,
        vector: object,
        k: int = 10,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return up to ``k`` ``(id, cosine)`` pairs sorted by descending cosine.

        If ``allowed_ids`` is provided, only those ids are considered. An empty
        index, an empty ``allowed_ids``, or a zero query vector yields ``[]``
        (a zero query has cosine 0 against everything, so nothing is "similar").
        """
        if len(self._ids) == 0 or k <= 0:
            return []
        if allowed_ids is not None and len(allowed_ids) == 0:
            return []
        q = _normalize(np.asarray(vector, dtype=np.float64).reshape(-1))
        if q.shape[0] != self.dim:
            raise ValueError(f"query dim {q.shape[0]} != index dim {self.dim}")
        if float(np.linalg.norm(q)) == 0.0:
            return []
        sims = self._matrix @ q  # rows already normalized -> cosine
        order = np.argsort(-sims, kind="stable")
        out: list[tuple[str, float]] = []
        for idx in order:
            did = self._ids[int(idx)]
            if allowed_ids is not None and did not in allowed_ids:
                continue
            out.append((did, float(sims[int(idx)])))
            if len(out) >= k:
                break
        return out

    def rebuild(self, items: list[tuple[str, object]]) -> None:
        """Replace the entire index contents from ``(id, vector)`` pairs."""
        self._ids = []
        self._pos = {}
        self._matrix = np.zeros((0, self.dim), dtype=np.float64)
        if not items:
            return
        rows = np.zeros((len(items), self.dim), dtype=np.float64)
        for i, (did, vec) in enumerate(items):
            row = _normalize(np.asarray(vec, dtype=np.float64).reshape(-1))
            if row.shape[0] != self.dim:
                raise ValueError(f"vector dim {row.shape[0]} != index dim {self.dim}")
            rows[i] = row
            self._pos[did] = i
            self._ids.append(did)
        self._matrix = rows

    def persist(self) -> None:
        """Write ids + matrix to ``self.path`` (atomic-ish via a temp file)."""
        ids = np.array(self._ids, dtype=object)
        tmp = f"{self.path}.tmp"
        with open(tmp, "wb") as fh:
            np.savez(fh, ids=ids, matrix=self._matrix, dim=np.array([self.dim]))
        os.replace(tmp, self.path)

    def load(self) -> None:
        """Load ids + matrix from ``self.path`` if it exists (else stay empty)."""
        if not os.path.exists(self.path):
            return
        with np.load(self.path, allow_pickle=True) as data:
            ids = list(data["ids"].tolist())
            matrix = np.asarray(data["matrix"], dtype=np.float64)
        # A persisted matrix whose dim no longer matches ``self.dim`` (e.g. the
        # embedding backend changed stub@256 <-> local@384 over an existing file)
        # would crash the dot product. The index is a rebuildable cache, so on a
        # dim mismatch we warn and start empty -- ``rebuild_index`` recovers it.
        if matrix.shape[0] > 0 and matrix.shape[1] != self.dim:
            logger.warning(
                "vector index at %s has dim %d != expected %d; treating as empty "
                "(rebuild to recover)",
                self.path,
                matrix.shape[1],
                self.dim,
            )
            self._ids = []
            self._pos = {}
            self._matrix = np.zeros((0, self.dim), dtype=np.float64)
            return
        self._ids = [str(x) for x in ids]
        self._pos = {did: i for i, did in enumerate(self._ids)}
        if matrix.shape[0] == 0:
            self._matrix = np.zeros((0, self.dim), dtype=np.float64)
        else:
            self._matrix = matrix


@runtime_checkable
class VectorIndexProtocol(Protocol):
    """The vector-index surface the repository depends on (ADR-0034).

    Both the brute-force :class:`VectorIndex` and the optional hnswlib-backed
    :class:`~hydromemory.storage.ann_index.AnnVectorIndex` satisfy it, so the
    backend is swappable without touching the repository, recall, or scoring.
    """

    def __len__(self) -> int: ...
    def add(self, id: str, vector: object) -> None: ...
    def remove(self, id: str) -> None: ...
    def search(
        self, vector: object, k: int = 10, allowed_ids: set[str] | None = None
    ) -> list[tuple[str, float]]: ...
    def rebuild(self, items: list[tuple[str, object]]) -> None: ...
    def persist(self) -> None: ...
    def load(self) -> None: ...


def build_vector_index(path: str, dim: int, *, backend: str = "brute") -> VectorIndexProtocol:
    """Build the configured vector index (ADR-0034).

    ``backend='brute'`` (default) is the exact, deterministic numpy
    :class:`VectorIndex` — the right choice for CI, tests, and small corpora.
    For sub-linear recall at scale, behind the *same* contract:
    ``'hnswlib'`` and ``'faiss'`` select the respective ANN backends in
    :mod:`hydromemory.storage.ann_index`, and ``'ann'`` picks the best available
    (hnswlib preferred, then faiss-cpu). A clear ``RuntimeError`` is raised only if an
    ANN backend is selected without its library installed.
    """
    normalized = (backend or "brute").strip().lower()
    if normalized in ("brute", "exact", "numpy", ""):
        return VectorIndex(path, dim)
    if normalized in ("hnsw", "hnswlib"):
        from hydromemory.storage.ann_index import AnnVectorIndex

        return AnnVectorIndex(path, dim)
    if normalized == "faiss":
        from hydromemory.storage.ann_index import FaissVectorIndex

        return FaissVectorIndex(path, dim)
    if normalized == "ann":
        import importlib.util

        from hydromemory.storage.ann_index import AnnVectorIndex, FaissVectorIndex

        if importlib.util.find_spec("hnswlib") is not None:
            return AnnVectorIndex(path, dim)
        if importlib.util.find_spec("faiss") is not None:
            return FaissVectorIndex(path, dim)
        raise RuntimeError(
            "vector backend 'ann' requires hnswlib or faiss-cpu; neither is installed "
            "(pip install faiss-cpu, or hnswlib via the '.[ann]' extra)"
        )
    raise ValueError(f"unknown vector backend {backend!r} (use 'brute', 'ann', 'hnswlib', or 'faiss')")
