"""Pluggable ANN vector-index backend (ADR-0034).

The *seam* (factory + config wiring + the absent-library errors) is always
verifiable. The hnswlib/faiss recall@k parity and the remove/replace/persist
round-trip run against whichever ANN backend is installed (parametrized) and skip
when none is — so the brute-force default keeps the suite green without a heavy dep.
"""
from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from hydromemory.config import HydroConfig
from hydromemory.storage import open_store
from hydromemory.storage.vector_index import VectorIndex, build_vector_index

_HAS_HNSWLIB = importlib.util.find_spec("hnswlib") is not None
_HAS_FAISS = importlib.util.find_spec("faiss") is not None
_ANN_BACKENDS = (["hnswlib"] if _HAS_HNSWLIB else []) + (["faiss"] if _HAS_FAISS else [])


# --- the seam (always verifiable) -------------------------------------------
def test_factory_default_is_brute(tmp_path):
    assert isinstance(build_vector_index(str(tmp_path / "x.npz"), 4), VectorIndex)


def test_factory_brute_explicit(tmp_path):
    assert isinstance(build_vector_index(str(tmp_path / "x.npz"), 4, backend="brute"), VectorIndex)


def test_factory_unknown_backend_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown vector backend"):
        build_vector_index(str(tmp_path / "x.npz"), 4, backend="bogus")


@pytest.mark.skipif(_HAS_HNSWLIB, reason="hnswlib installed; absent-lib error path not exercised")
def test_factory_hnswlib_without_lib_raises_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="hnswlib"):
        build_vector_index(str(tmp_path / "x.npz"), 4, backend="hnswlib")


@pytest.mark.skipif(_HAS_FAISS, reason="faiss installed; absent-lib error path not exercised")
def test_factory_faiss_without_lib_raises_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="faiss"):
        build_vector_index(str(tmp_path / "x.npz"), 4, backend="faiss")


@pytest.mark.skipif(not _ANN_BACKENDS, reason="no ANN backend installed (hnswlib/faiss)")
def test_factory_ann_alias_picks_available_backend(tmp_path):
    idx = build_vector_index(str(tmp_path / "x.npz"), 4, backend="ann")
    assert not isinstance(idx, VectorIndex)  # an ANN backend, not brute-force


def test_open_store_defaults_to_brute(tmp_path):
    store = open_store(HydroConfig(db_path=str(tmp_path / "a.db"), vector_dim=8))
    try:
        assert isinstance(store._index, VectorIndex)  # default backend is brute-force
    finally:
        store.close()


def test_config_vector_backend_from_env(monkeypatch):
    monkeypatch.setenv("HYDRO_VECTOR_BACKEND", "ann")
    assert HydroConfig.from_env().vector_backend == "ann"
    monkeypatch.delenv("HYDRO_VECTOR_BACKEND", raising=False)
    assert HydroConfig.from_env().vector_backend == "brute"  # default


# --- ANN backends, exercised for real when installed ------------------------
@pytest.mark.skipif(not _ANN_BACKENDS, reason="requires an ANN backend (hnswlib/faiss)")
@pytest.mark.parametrize("backend", _ANN_BACKENDS)
def test_ann_matches_brute_recall_at_k(backend, tmp_path):
    rng = np.random.default_rng(0)
    dim, n, k = 16, 200, 10
    vectors = {f"v{i}": rng.standard_normal(dim) for i in range(n)}

    brute = build_vector_index(str(tmp_path / "b.npz"), dim, backend="brute")
    ann = build_vector_index(str(tmp_path / f"{backend}.npz"), dim, backend=backend)
    for did, vec in vectors.items():
        brute.add(did, vec)
        ann.add(did, vec)

    query = rng.standard_normal(dim)
    brute_ids = [did for did, _ in brute.search(query, k)]
    ann_ids = [did for did, _ in ann.search(query, k)]
    # Approximate recall: the ANN backend recovers most of the exact top-k.
    assert len(set(brute_ids) & set(ann_ids)) >= k - 2


@pytest.mark.skipif(not _ANN_BACKENDS, reason="requires an ANN backend (hnswlib/faiss)")
@pytest.mark.parametrize("backend", _ANN_BACKENDS)
def test_ann_remove_replace_persist_roundtrip(backend, tmp_path):
    rng = np.random.default_rng(1)
    dim = 8
    path = str(tmp_path / f"rt_{backend}.npz")
    idx = build_vector_index(path, dim, backend=backend)
    vectors = {f"v{i}": rng.standard_normal(dim) for i in range(20)}
    for did, vec in vectors.items():
        idx.add(did, vec)
    assert len(idx) == 20

    idx.remove("v5")  # soft delete -> excluded from results
    assert len(idx) == 19
    assert "v5" not in {i for i, _ in idx.search(rng.standard_normal(dim), 20)}

    idx.add("v6", rng.standard_normal(dim))  # replace -> still one live entry
    assert len(idx) == 19

    idx.persist()
    reloaded = build_vector_index(path, dim, backend=backend)
    assert len(reloaded) == 19
    reloaded_ids = {i for i, _ in reloaded.search(vectors["v0"], 19)}
    assert "v0" in reloaded_ids and "v5" not in reloaded_ids  # survives reload; delete sticks
