"""Optional ANN-backed vector indexes (hnswlib and faiss) — ADR-0034.

Two interchangeable backends, both pluggable *behind the same contract* as the
brute-force :class:`~hydromemory.storage.vector_index.VectorIndex` (same
``add``/``remove``/``search``/``rebuild``/``persist``/``load``/``__len__`` surface
and the ``(id, cosine)`` result shape), selected via ``config.vector_backend`` through
:func:`~hydromemory.storage.vector_index.build_vector_index` (``'hnswlib'``, ``'faiss'``,
or ``'ann'`` = best available).

Both libraries are OPTIONAL and **lazy-imported**, so importing this module never
requires either and the offline/default brute-force path is unaffected. ``hnswlib``
builds from source on Windows (needs MSVC); ``faiss-cpu`` ships binary wheels, so it
is the wheel-friendly choice. Whichever is installed, the recall@k parity test
(``tests/test_ann_index.py``) validates the backend against brute-force; with neither
installed the parity test skips (the brute-force default keeps the suite green).

Known limitations (documented, ADR-0034): recall is *approximate* (guarded by the
parity test); under an ``allowed_ids`` filter we oversample then post-filter, so a
filtered query is best-effort; replacement marks the old label deleted and adds a
new one (hnswlib has no in-place vector replace).
"""
from __future__ import annotations

import json
import os
from typing import Any

import numpy as np


def _require_hnswlib() -> Any:
    try:
        import hnswlib
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "hnswlib ANN backend requires hnswlib (pip install hnswlib, or the '.[ann]' "
            "extra). On Windows hnswlib builds from source (needs MSVC); 'faiss' is a "
            "wheel-only alternative ANN backend. The default 'brute' backend needs no extra."
        ) from exc
    return hnswlib


def _require_faiss() -> Any:
    try:
        import faiss
    except ImportError as exc:  # pragma: no cover - exercised only without the lib
        raise RuntimeError(
            "faiss ANN backend requires faiss-cpu: pip install faiss-cpu. "
            "The default 'brute' backend needs no extra."
        ) from exc
    return faiss


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec if norm == 0.0 else vec / norm


class AnnVectorIndex:
    """hnswlib (cosine) ANN index with the :class:`VectorIndex` method surface."""

    def __init__(
        self,
        path: str,
        dim: int,
        *,
        ef_construction: int = 200,
        m: int = 16,
        ef: int = 64,
    ) -> None:
        self._hnswlib = _require_hnswlib()  # raises a clear error if the extra is absent
        self.path = path
        self.dim = int(dim)
        self._ef_construction = ef_construction
        self._m = m
        self._ef = ef
        self._capacity = 0
        self._next_label = 0
        self._label_of: dict[str, int] = {}
        self._id_of: dict[int, str] = {}
        self._index: Any = None
        self._init_index(1024)
        self.load()

    def _init_index(self, capacity: int) -> None:
        capacity = max(1, capacity)
        idx = self._hnswlib.Index(space="cosine", dim=self.dim)
        idx.init_index(max_elements=capacity, ef_construction=self._ef_construction, M=self._m)
        idx.set_ef(max(self._ef, 1))
        self._index = idx
        self._capacity = capacity

    def __len__(self) -> int:
        return len(self._label_of)

    def _ensure_capacity(self, extra: int = 1) -> None:
        need = len(self._label_of) + extra
        if need > self._capacity:
            new_capacity = max(need, self._capacity * 2)
            self._index.resize_index(new_capacity)
            self._capacity = new_capacity

    def add(self, id: str, vector: object) -> None:
        row = _normalize(np.asarray(vector, dtype=np.float32).reshape(-1))
        if row.shape[0] != self.dim:
            raise ValueError(f"vector dim {row.shape[0]} != index dim {self.dim}")
        old = self._label_of.get(id)
        if old is not None:  # hnswlib has no in-place replace: delete old, add new label
            try:
                self._index.mark_deleted(old)
            except Exception:  # pragma: no cover - already-deleted is harmless
                pass
            self._id_of.pop(old, None)
        label = self._next_label
        self._next_label += 1
        self._ensure_capacity(1)
        self._index.add_items(row.reshape(1, -1), np.array([label]))
        self._label_of[id] = label
        self._id_of[label] = id

    def remove(self, id: str) -> None:
        label = self._label_of.pop(id, None)
        if label is None:
            return
        self._id_of.pop(label, None)
        try:
            self._index.mark_deleted(label)
        except Exception:  # pragma: no cover
            pass

    def search(
        self, vector: object, k: int = 10, allowed_ids: set[str] | None = None
    ) -> list[tuple[str, float]]:
        if len(self._label_of) == 0 or k <= 0:
            return []
        if allowed_ids is not None and len(allowed_ids) == 0:
            return []
        q = _normalize(np.asarray(vector, dtype=np.float32).reshape(-1))
        if q.shape[0] != self.dim:
            raise ValueError(f"query dim {q.shape[0]} != index dim {self.dim}")
        if float(np.linalg.norm(q)) == 0.0:
            return []
        # Oversample so post-filtering (deleted labels + allowed_ids) still yields k.
        margin = max(k * 4, k + 64) if allowed_ids is not None else max(k * 2, k)
        want = min(len(self._label_of), margin)
        labels, distances = self._index.knn_query(q.reshape(1, -1), k=max(1, want))
        out: list[tuple[str, float]] = []
        for label, dist in zip(labels[0].tolist(), distances[0].tolist(), strict=False):
            did = self._id_of.get(int(label))
            if did is None:
                continue  # deleted or unknown label
            if allowed_ids is not None and did not in allowed_ids:
                continue
            out.append((did, 1.0 - float(dist)))  # cosine space: cosine = 1 - distance
            if len(out) >= k:
                break
        return out

    def rebuild(self, items: list[tuple[str, object]]) -> None:
        self._label_of = {}
        self._id_of = {}
        self._next_label = 0
        self._init_index(max(1024, (len(items) * 2) or 1024))
        for did, vec in items:
            self.add(did, vec)

    def persist(self) -> None:
        self._index.save_index(self.path + ".hnsw")
        meta = {
            "dim": self.dim,
            "next_label": self._next_label,
            "capacity": self._capacity,
            "label_of": self._label_of,
        }
        tmp = self.path + ".meta.json.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(meta, handle)
        os.replace(tmp, self.path + ".meta.json")

    def load(self) -> None:
        meta_path = self.path + ".meta.json"
        index_path = self.path + ".hnsw"
        if not (os.path.exists(meta_path) and os.path.exists(index_path)):
            return
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
        # Dim mismatch: the index is a rebuildable cache, so stay empty (mirror
        # VectorIndex.load) — rebuild_index recovers it.
        if int(meta.get("dim", self.dim)) != self.dim:
            return
        self._next_label = int(meta.get("next_label", 0))
        self._capacity = int(meta.get("capacity", 1024))
        self._label_of = {str(key): int(value) for key, value in meta.get("label_of", {}).items()}
        self._id_of = {value: key for key, value in self._label_of.items()}
        self._index.load_index(index_path, max_elements=max(self._capacity, len(self._label_of) + 1))
        self._index.set_ef(max(self._ef, 1))


class FaissVectorIndex:
    """faiss (HNSW, inner-product on normalized vectors = cosine) ANN index.

    A wheel-friendly alternative to hnswlib with the same :class:`VectorIndex`
    surface. faiss HNSW has no native delete, so removal/replacement is a
    *soft delete*: the position is recorded and filtered out of results (and
    dropped on ``rebuild``). lazy-imported (``faiss-cpu``).
    """

    def __init__(
        self,
        path: str,
        dim: int,
        *,
        m: int = 32,
        ef_construction: int = 200,
        ef_search: int = 64,
    ) -> None:
        self._faiss = _require_faiss()
        self.path = path
        self.dim = int(dim)
        self._m = m
        self._ef_construction = ef_construction
        self._ef_search = ef_search
        self._labels: list[str] = []  # faiss internal position -> string id
        self._pos_of: dict[str, int] = {}  # string id -> current live position
        self._deleted: set[int] = set()  # soft-deleted positions
        self._index: Any = None
        self._new_index()
        self.load()

    def _new_index(self) -> None:
        idx = self._faiss.IndexHNSWFlat(self.dim, self._m, self._faiss.METRIC_INNER_PRODUCT)
        idx.hnsw.efConstruction = self._ef_construction
        idx.hnsw.efSearch = self._ef_search
        self._index = idx

    def __len__(self) -> int:
        return len(self._pos_of)

    def add(self, id: str, vector: object) -> None:
        row = _normalize(np.asarray(vector, dtype=np.float32).reshape(-1))
        if row.shape[0] != self.dim:
            raise ValueError(f"vector dim {row.shape[0]} != index dim {self.dim}")
        old = self._pos_of.get(id)
        if old is not None:
            self._deleted.add(old)  # soft-delete the prior vector (no native replace)
        self._index.add(row.reshape(1, -1))  # appends at position len(self._labels)
        position = len(self._labels)
        self._labels.append(id)
        self._pos_of[id] = position

    def remove(self, id: str) -> None:
        position = self._pos_of.pop(id, None)
        if position is not None:
            self._deleted.add(position)

    def search(
        self, vector: object, k: int = 10, allowed_ids: set[str] | None = None
    ) -> list[tuple[str, float]]:
        if len(self._pos_of) == 0 or k <= 0:
            return []
        if allowed_ids is not None and len(allowed_ids) == 0:
            return []
        q = _normalize(np.asarray(vector, dtype=np.float32).reshape(-1))
        if q.shape[0] != self.dim:
            raise ValueError(f"query dim {q.shape[0]} != index dim {self.dim}")
        if float(np.linalg.norm(q)) == 0.0:
            return []
        # Oversample so soft-deleted positions + allowed_ids filtering still yield k.
        margin = max(k * 4, k + 64) if allowed_ids is not None else max(k * 2, k)
        want = min(len(self._labels), margin)
        self._index.hnsw.efSearch = max(self._ef_search, want)
        scores, positions = self._index.search(q.reshape(1, -1), max(1, want))
        out: list[tuple[str, float]] = []
        for score, position in zip(scores[0].tolist(), positions[0].tolist(), strict=False):
            if position < 0 or position in self._deleted:
                continue  # faiss pads with -1; skip soft-deleted slots
            did = self._labels[position]
            if self._pos_of.get(did) != position:
                continue  # stale position (id was replaced/removed)
            if allowed_ids is not None and did not in allowed_ids:
                continue
            out.append((did, float(score)))  # inner product on normalized = cosine
            if len(out) >= k:
                break
        return out

    def rebuild(self, items: list[tuple[str, object]]) -> None:
        self._labels = []
        self._pos_of = {}
        self._deleted = set()
        self._new_index()
        for did, vec in items:
            self.add(did, vec)

    def persist(self) -> None:
        self._faiss.write_index(self._index, self.path + ".faiss")
        meta = {
            "dim": self.dim,
            "labels": self._labels,
            "deleted": sorted(self._deleted),
        }
        tmp = self.path + ".faiss.meta.json.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(meta, handle)
        os.replace(tmp, self.path + ".faiss.meta.json")

    def load(self) -> None:
        meta_path = self.path + ".faiss.meta.json"
        index_path = self.path + ".faiss"
        if not (os.path.exists(meta_path) and os.path.exists(index_path)):
            return
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
        if int(meta.get("dim", self.dim)) != self.dim:
            return  # dim mismatch -> stay empty (rebuildable cache)
        self._index = self._faiss.read_index(index_path)
        self._index.hnsw.efSearch = self._ef_search
        self._labels = [str(x) for x in meta.get("labels", [])]
        self._deleted = {int(x) for x in meta.get("deleted", [])}
        self._pos_of = {}
        for position, did in enumerate(self._labels):
            if position not in self._deleted:
                self._pos_of[did] = position  # last live position wins


__all__ = ["AnnVectorIndex", "FaissVectorIndex"]
