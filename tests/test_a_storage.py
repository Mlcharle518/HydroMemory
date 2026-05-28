"""Track A storage tests: SqliteDropletRepository + VectorIndex.

Covers repo CRUD, every ``query`` filter, the link graph (add/remove reflected on
``get``), ``touch_cycle``, persistence across reopen, and the vector index
(persist/reload/rebuild, cosine ordering, ``allowed_ids``/``candidate_filter``).
"""
from __future__ import annotations

import os

import pytest

from hydromemory.intelligence.stub import StubEmbedder
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, Visibility
from hydromemory.storage import SqliteDropletRepository, open_store
from hydromemory.storage.vector_index import VectorIndex


def _droplet(did: str, **kw):
    base = {"id": did, "content": kw.pop("content", f"content for {did}")}
    base.update(kw)
    return Droplet.from_dict(base)


# --------------------------------------------------------------------- CRUD
def test_open_store_returns_repository(config):
    store = open_store(config)
    try:
        assert isinstance(store, SqliteDropletRepository)
    finally:
        store.close()


def test_upsert_get_roundtrip(config):
    store = open_store(config)
    try:
        d = _droplet(
            "mem_a",
            content="User prefers architectural depth.",
            reservoir="surface",
            phase="liquid",
            memory_type="communication_preference",
            semantic_tags=["depth", "architecture"],
            state={"purity": 0.91, "confidence": 0.8},
        )
        store.upsert(d)
        got = store.get("mem_a")
        assert got is not None
        assert got.id == "mem_a"
        assert got.content == "User prefers architectural depth."
        assert got.reservoir is Reservoir.SURFACE
        assert got.phase is Phase.LIQUID
        assert got.memory_type == "communication_preference"
        assert got.semantic_tags == ["depth", "architecture"]
        assert got.state.purity == 0.91
    finally:
        store.close()


def test_get_missing_returns_none(config):
    store = open_store(config)
    try:
        assert store.get("nope") is None
    finally:
        store.close()


def test_upsert_updates_existing(config):
    store = open_store(config)
    try:
        store.upsert(_droplet("mem_u", content="v1", phase="liquid"))
        store.upsert(_droplet("mem_u", content="v2", phase="vapor"))
        got = store.get("mem_u")
        assert got.content == "v2"
        assert got.phase is Phase.VAPOR
        assert store.all_ids() == ["mem_u"]
    finally:
        store.close()


def test_delete_removes_row_links_and_vector(config):
    store = open_store(config)
    try:
        d = _droplet("mem_d", reservoir="surface")
        d.embedding = StubEmbedder(config.vector_dim).embed("delete me please")
        store.upsert(d)
        store.add_link("mem_d", "associations", "mem_other")
        store.add_link("mem_other", "supports", "mem_d")
        store.delete("mem_d")
        assert store.get("mem_d") is None
        assert "mem_d" not in store.all_ids()
        # vector gone -> no similarity hit for its own embedding
        assert store.search_similar(d.embedding, k=5) == []
        # links referencing mem_d (both directions) removed
        store.upsert(_droplet("mem_other"))
        assert store.get("mem_other").links.supports == []
    finally:
        store.close()


def test_all_ids_insertion_order(config):
    store = open_store(config)
    try:
        for did in ["mem_1", "mem_2", "mem_3"]:
            store.upsert(_droplet(did))
        assert store.all_ids() == ["mem_1", "mem_2", "mem_3"]
    finally:
        store.close()


# -------------------------------------------------------------------- query
def test_query_every_filter(config):
    store = open_store(config)
    try:
        store.upsert(_droplet("s1", reservoir="surface", phase="liquid",
                              memory_type="value", state={"purity": 0.9},
                              permissions={"visibility": "public", "owner": "user"}))
        store.upsert(_droplet("g1", reservoir="groundwater", phase="vapor",
                              memory_type="factual", state={"purity": 0.4},
                              permissions={"visibility": "private",
                                           "allowed_agents": ["agent_x"]}))
        store.upsert(_droplet("c1", reservoir="contaminated", phase="polluted",
                              memory_type="factual", state={"purity": 0.1}))

        assert {d.id for d in store.query(reservoir=Reservoir.SURFACE)} == {"s1"}
        assert {d.id for d in store.query(phase=Phase.POLLUTED)} == {"c1"}
        assert {d.id for d in store.query(memory_type="factual")} == {"g1", "c1"}
        assert {d.id for d in store.query(min_purity=0.5)} == {"s1"}
        assert {d.id for d in store.query(visibility=Visibility.PUBLIC)} == {"s1"}
        # usable_for_response_only excludes contaminated reservoir AND polluted phase
        usable = {d.id for d in store.query(usable_for_response_only=True)}
        assert usable == {"s1", "g1"}
        # allowed_agent: explicit grant OR public user memory
        assert {d.id for d in store.query(allowed_agent="agent_x")} == {"s1", "g1"}
        # limit
        assert len(store.query(limit=2)) == 2
    finally:
        store.close()


def test_query_combined_filters(config):
    store = open_store(config)
    try:
        store.upsert(_droplet("a", reservoir="surface", phase="liquid",
                              state={"purity": 0.95}))
        store.upsert(_droplet("b", reservoir="surface", phase="liquid",
                              state={"purity": 0.2}))
        out = store.query(reservoir=Reservoir.SURFACE, min_purity=0.5)
        assert [d.id for d in out] == ["a"]
    finally:
        store.close()


# --------------------------------------------------------------------- links
def test_link_graph_add_remove_reflected_on_get(config):
    store = open_store(config)
    try:
        store.upsert(_droplet("src"))
        store.add_link("src", "associations", "dst1")
        store.add_link("src", "associations", "dst2")
        store.add_link("src", "contradictions", "dst3")
        got = store.get("src")
        assert got.links.associations == ["dst1", "dst2"]
        assert got.links.contradictions == ["dst3"]

        store.remove_link("src", "associations", "dst1")
        got = store.get("src")
        assert got.links.associations == ["dst2"]
    finally:
        store.close()


def test_add_link_idempotent(config):
    store = open_store(config)
    try:
        store.upsert(_droplet("src"))
        store.add_link("src", "supports", "x")
        store.add_link("src", "supports", "x")
        assert store.get("src").links.supports == ["x"]
    finally:
        store.close()


def test_add_link_rejects_unknown_kind(config):
    store = open_store(config)
    try:
        store.upsert(_droplet("src"))
        with pytest.raises(ValueError):
            store.add_link("src", "bogus", "x")
    finally:
        store.close()


def test_links_are_source_of_truth_synced_on_upsert(config):
    store = open_store(config)
    try:
        d = _droplet("src", links={"associations": ["a", "b"], "supports": ["c"]})
        store.upsert(d)
        got = store.get("src")
        assert got.links.associations == ["a", "b"]
        assert got.links.supports == ["c"]
        # Re-upsert with a changed link set replaces the table rows.
        d2 = _droplet("src", links={"associations": ["z"]})
        store.upsert(d2)
        got2 = store.get("src")
        assert got2.links.associations == ["z"]
        assert got2.links.supports == []
    finally:
        store.close()


# --------------------------------------------------------------------- cycle
def test_touch_cycle(config):
    from datetime import UTC, datetime

    store = open_store(config)
    try:
        store.upsert(_droplet("mem_c"))
        ts = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
        store.touch_cycle("mem_c", recalled=ts, increment_count=True)
        got = store.get("mem_c")
        assert got.cycle.cycle_count == 1
        assert got.cycle.last_recalled == ts
        # second touch increments again, sets transformed, leaves recalled
        store.touch_cycle("mem_c", transformed=ts, increment_count=True)
        got = store.get("mem_c")
        assert got.cycle.cycle_count == 2
        assert got.cycle.last_transformed == ts
        assert got.cycle.last_recalled == ts
    finally:
        store.close()


def test_touch_cycle_missing_is_noop(config):
    store = open_store(config)
    try:
        store.touch_cycle("ghost", increment_count=True)  # must not raise
    finally:
        store.close()


# --------------------------------------------------------------- persistence
def test_persistence_across_reopen(config):
    store = open_store(config)
    emb = StubEmbedder(config.vector_dim).embed("persist this droplet")
    try:
        d = _droplet("mem_p", reservoir="groundwater", phase="vapor",
                     memory_type="value", state={"purity": 0.77})
        d.embedding = emb
        store.upsert(d)
        store.add_link("mem_p", "derived_from", "mem_root")
    finally:
        store.close()

    reopened = open_store(config)
    try:
        got = reopened.get("mem_p")
        assert got is not None
        assert got.reservoir is Reservoir.GROUNDWATER
        assert got.memory_type == "value"
        assert got.state.purity == 0.77
        assert got.links.derived_from == ["mem_root"]
        assert got.embedding is not None
        # vector index reloaded from disk -> similarity still works
        hits = reopened.search_similar(emb, k=3)
        assert hits and hits[0][0] == "mem_p"
    finally:
        reopened.close()


# ----------------------------------------------------------- search_similar
def test_search_similar_cosine_ordering(config):
    store = open_store(config)
    embedder = StubEmbedder(config.vector_dim)
    try:
        store.upsert(_droplet_with_emb("near", "depth architecture systems thinking", embedder))
        store.upsert(_droplet_with_emb("mid", "depth architecture cats", embedder))
        store.upsert(_droplet_with_emb("far", "completely unrelated banana topic", embedder))
        q = embedder.embed("depth architecture systems thinking")
        ranked = store.search_similar(q, k=3)
        ids = [r[0] for r in ranked]
        assert ids[0] == "near"
        # cosine should be sorted descending
        cosines = [r[1] for r in ranked]
        assert cosines == sorted(cosines, reverse=True)
        # shared-word doc beats unrelated doc
        assert ids.index("mid") < ids.index("far")
    finally:
        store.close()


def test_search_similar_candidate_filter(config):
    store = open_store(config)
    embedder = StubEmbedder(config.vector_dim)
    try:
        store.upsert(_droplet_with_emb("usable", "alpha beta gamma", embedder,
                                       reservoir="surface", phase="liquid"))
        store.upsert(_droplet_with_emb("poison", "alpha beta gamma", embedder,
                                       reservoir="contaminated", phase="polluted"))
        q = embedder.embed("alpha beta gamma")
        out = store.search_similar(
            q, k=5,
            candidate_filter=lambda d: d.reservoir is not Reservoir.CONTAMINATED,
        )
        ids = [r[0] for r in out]
        assert "usable" in ids
        assert "poison" not in ids
    finally:
        store.close()


def test_search_similar_k_limit(config):
    store = open_store(config)
    embedder = StubEmbedder(config.vector_dim)
    try:
        for i in range(5):
            store.upsert(_droplet_with_emb(f"m{i}", f"shared word token{i}", embedder))
        q = embedder.embed("shared word")
        assert len(store.search_similar(q, k=2)) == 2
    finally:
        store.close()


def test_rebuild_index_recovers_after_vec_file_deleted(config):
    store = open_store(config)
    embedder = StubEmbedder(config.vector_dim)
    emb = embedder.embed("rebuild me")
    try:
        d = _droplet("mem_r")
        d.embedding = emb
        store.upsert(d)
    finally:
        store.close()

    # Nuke the vector file; rows (with embedding stashed in meta) remain.
    vec_path = f"{config.db_path}.vec.npz"
    assert os.path.exists(vec_path)
    os.remove(vec_path)

    reopened = open_store(config)
    try:
        # Index empty right after load (file was gone)...
        assert reopened.search_similar(emb, k=3) == []
        # ...rebuild from stored droplets restores it.
        reopened.rebuild_index()
        hits = reopened.search_similar(emb, k=3)
        assert hits and hits[0][0] == "mem_r"
    finally:
        reopened.close()


# ------------------------------------------------- VectorIndex (direct unit)
def test_vector_index_persist_reload(tmp_path):
    path = str(tmp_path / "idx.npz")
    idx = VectorIndex(path, dim=4)
    idx.add("a", [1.0, 0.0, 0.0, 0.0])
    idx.add("b", [0.0, 1.0, 0.0, 0.0])
    idx.persist()

    reloaded = VectorIndex(path, dim=4)
    reloaded.load()
    assert len(reloaded) == 2
    hits = reloaded.search([1.0, 0.0, 0.0, 0.0], k=2)
    assert hits[0][0] == "a"
    assert hits[0][1] == pytest.approx(1.0)


def test_vector_index_allowed_ids(tmp_path):
    idx = VectorIndex(str(tmp_path / "i.npz"), dim=3)
    idx.add("a", [1.0, 0.0, 0.0])
    idx.add("b", [0.9, 0.1, 0.0])
    idx.add("c", [0.0, 0.0, 1.0])
    hits = idx.search([1.0, 0.0, 0.0], k=5, allowed_ids={"b", "c"})
    ids = [h[0] for h in hits]
    assert "a" not in ids
    assert ids[0] == "b"


def test_vector_index_empty_and_zero_vector(tmp_path):
    idx = VectorIndex(str(tmp_path / "e.npz"), dim=3)
    assert idx.search([1.0, 0.0, 0.0], k=3) == []  # empty index
    idx.add("z", [0.0, 0.0, 0.0])  # zero vector tolerated
    assert len(idx) == 1
    # zero query vector -> no results
    assert idx.search([0.0, 0.0, 0.0], k=3) == []
    # empty allowed set -> no results
    idx.add("a", [1.0, 0.0, 0.0])
    assert idx.search([1.0, 0.0, 0.0], k=3, allowed_ids=set()) == []


def test_vector_index_rebuild_and_remove(tmp_path):
    idx = VectorIndex(str(tmp_path / "r.npz"), dim=2)
    idx.rebuild([("a", [1.0, 0.0]), ("b", [0.0, 1.0]), ("c", [1.0, 1.0])])
    assert len(idx) == 3
    idx.remove("b")
    assert len(idx) == 2
    ids = [h[0] for h in idx.search([1.0, 0.0], k=5)]
    assert "b" not in ids and "a" in ids and "c" in ids
    idx.remove("missing")  # no-op
    assert len(idx) == 2


def test_vector_index_add_replaces_existing(tmp_path):
    idx = VectorIndex(str(tmp_path / "x.npz"), dim=2)
    idx.add("a", [1.0, 0.0])
    idx.add("a", [0.0, 1.0])
    assert len(idx) == 1
    hits = idx.search([0.0, 1.0], k=1)
    assert hits[0][0] == "a"
    assert hits[0][1] == pytest.approx(1.0)


# ----------------------------------------------------------------- helpers
def _droplet_with_emb(did: str, text: str, embedder: StubEmbedder, **kw):
    d = _droplet(did, content=text, **kw)
    d.embedding = embedder.embed(text)
    return d
