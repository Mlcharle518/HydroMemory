"""v2 §9 User-Controlled Memory Vault tests (Phase B1, Vault track).

Covers the four vault guarantees layered over the plain ``SqliteDropletRepository``:

* encryption-at-rest — Fernet round-trip (content column is ciphertext on disk,
  ``get`` decrypts it back) and the keyless NullCipher round-trip;
* tamper-evident audit — entries written for upsert/get/query, ``verify_chain``
  True then False after a direct ``UPDATE`` to an audit row;
* L1 app isolation — a vault scoped to app "a" never sees app "b" droplets;
* access enforcement — a denied read is audited (and returns nothing);
* the vector index survives encryption — ``search_similar`` + ``rebuild_index``
  still return the correct, cosine-ordered hits.
"""
from __future__ import annotations

import sqlite3

import pytest

from hydromemory.config import HydroConfig
from hydromemory.governance import AccessContext, AgentIdentity, TrustLevel
from hydromemory.intelligence import build_intelligence
from hydromemory.schema import Droplet
from hydromemory.vault import AppScope, build_vault_engine, open_vault_store
from hydromemory.vault.audit import AuditLog


# --------------------------------------------------------------------- helpers
def _config(tmp_path, *, key: str | None = None) -> HydroConfig:
    return HydroConfig(
        db_path=str(tmp_path / "vault.db"),
        vector_dim=64,
        intelligence_backend="stub",
        vault_key=key,
    )


def _droplet(did: str, *, content: str, reservoir: str = "surface", **state) -> Droplet:
    return Droplet.from_dict(
        {
            "id": did,
            "content": content,
            "reservoir": reservoir,
            "state": {"purity": 0.9, **state},
        }
    )


def _raw_row(db_path: str, did: str) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM droplets WHERE id = ?", (did,)).fetchone()
    finally:
        conn.close()


# ------------------------------------------------------------- encryption (Fernet)
def test_fernet_roundtrip_content_is_ciphertext_on_disk(tmp_path):
    cfg = _config(tmp_path, key="a-secret-passphrase")
    store = open_vault_store(cfg)
    try:
        store.upsert(
            _droplet(
                "mem_1",
                content="User's home address is 12 Privacy Lane.",
                confidence=0.83,
            )
        )

        # On disk: content column is ciphertext, never the plaintext.
        row = _raw_row(cfg.db_path, "mem_1")
        assert row is not None
        assert "Privacy Lane" not in row["content"]
        assert row["content"] != "User's home address is 12 Privacy Lane."

        # get() decrypts back to the original droplet.
        got = store.get("mem_1")
        assert got is not None
        assert got.content == "User's home address is 12 Privacy Lane."
        assert got.state.confidence == pytest.approx(0.83)
    finally:
        store.close()


def test_fernet_encrypts_state_tags_meta_but_keeps_routing_plaintext(tmp_path):
    cfg = _config(tmp_path, key="key-123")
    store = open_vault_store(cfg)
    try:
        d = Droplet.from_dict(
            {
                "id": "mem_2",
                "content": "secret content",
                "reservoir": "surface",
                "phase": "liquid",
                "memory_type": "value",
                "semantic_tags": ["trauma", "private"],
                "state": {"purity": 0.77, "confidence": 0.9, "salinity": 0.42},
                "meta": {"sensitivity": 0.9},
            }
        )
        store.upsert(d)
        row = _raw_row(cfg.db_path, "mem_2")

        # Routing/governance columns stay queryable (plaintext).
        assert row["phase"] == "liquid"
        assert row["reservoir"] == "surface"
        assert row["memory_type"] == "value"
        assert row["owner"] == "user"
        assert row["visibility"] == "private"
        assert row["purity"] == pytest.approx(0.77)

        # Secret fields are NOT present in plaintext on disk.
        assert "trauma" not in (row["semantic_tags_json"] or "")
        assert "0.42" not in (row["state_json"] or "")  # salinity hidden
        assert "sensitivity" not in (row["meta_json"] or "")

        # Round-trip restores everything.
        got = store.get("mem_2")
        assert got.semantic_tags == ["trauma", "private"]
        assert got.state.salinity == pytest.approx(0.42)
        assert got.meta.get("sensitivity") == 0.9
    finally:
        store.close()


# --------------------------------------------------------- encryption (NullCipher)
def test_nullcipher_roundtrip_keyless(tmp_path):
    cfg = _config(tmp_path, key=None)  # no key -> NullCipher
    store = open_vault_store(cfg)
    try:
        assert store.cipher.label == "null-dev"
        store.upsert(_droplet("mem_n", content="plaintext under null cipher"))
        got = store.get("mem_n")
        assert got is not None
        assert got.content == "plaintext under null cipher"

        # NullCipher stores the payload verbatim, so the plaintext is recoverable
        # from the on-disk content column (documented: dev-only, NOT secure).
        row = _raw_row(cfg.db_path, "mem_n")
        assert "plaintext under null cipher" in row["content"]
    finally:
        store.close()


# ----------------------------------------------------------------------- audit
def test_audit_entries_written_for_upsert_get_query(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    try:
        store.upsert(_droplet("mem_a", content="hello"))
        store.get("mem_a")
        store.query()

        ops = [e.operation for e in store.audit.query()]
        assert "mutate" in ops  # upsert
        assert ops.count("read") >= 2  # get + query
        assert all(e.allowed for e in store.audit.query())
    finally:
        store.close()


def test_verify_chain_true_then_false_after_tamper(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    try:
        store.upsert(_droplet("mem_a", content="one"))
        store.upsert(_droplet("mem_b", content="two"))
        store.get("mem_a")
        assert store.audit.verify_chain() is True

        # Tamper with a committed audit row -> chain no longer recomputes.
        conn = store.backing._conn
        conn.execute("UPDATE audit SET detail = ? WHERE seq = 1", ("tampered",))
        conn.commit()
        assert store.audit.verify_chain() is False
    finally:
        store.close()


def test_audit_log_is_idempotent_on_reopen(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    store.upsert(_droplet("mem_a", content="one"))
    store.close()
    # Re-opening creates AuditLog again (CREATE TABLE IF NOT EXISTS) without error
    # and preserves the existing chain.
    store2 = open_vault_store(cfg)
    try:
        assert store2.audit.verify_chain() is True
        assert len(store2.audit.query()) >= 1
    finally:
        store2.close()


# ------------------------------------------------------------------ L1 isolation
def test_l1_app_scope_isolation(tmp_path):
    cfg = _config(tmp_path, key="k")
    store_a = open_vault_store(cfg, scope=AppScope(app_id="a"))
    store_b = open_vault_store(cfg, scope=AppScope(app_id="b"))
    try:
        store_a.upsert(_droplet("a1", content="app a memory"))
        store_b.upsert(_droplet("b1", content="app b memory"))

        # Each app sees only its own droplet via id-listing, get, and query.
        assert store_a.all_ids() == ["a1"]
        assert store_b.all_ids() == ["b1"]
        assert store_a.get("b1") is None
        assert store_b.get("a1") is None
        assert [d.id for d in store_a.query()] == ["a1"]
        assert [d.id for d in store_b.query()] == ["b1"]
    finally:
        store_a.close()
        store_b.close()


def test_l2_cross_app_owner_vault_sees_all(tmp_path):
    cfg = _config(tmp_path, key="k")
    store_a = open_vault_store(cfg, scope=AppScope(app_id="a"))
    store_b = open_vault_store(cfg, scope=AppScope(app_id="b"))
    owner = open_vault_store(cfg, scope=AppScope(cross_app=True))
    try:
        store_a.upsert(_droplet("a1", content="app a memory"))
        store_b.upsert(_droplet("b1", content="app b memory"))

        ids = set(owner.all_ids())
        assert {"a1", "b1"} <= ids
        assert owner.get("a1") is not None
        assert owner.get("b1") is not None
    finally:
        store_a.close()
        store_b.close()
        owner.close()


# -------------------------------------------------------------- access denied
def test_access_denied_is_audited(tmp_path):
    cfg = _config(tmp_path, key="k")
    # Writer is the default user-proxy (high trust) — may write groundwater.
    writer = open_vault_store(cfg)
    # Reader is a weak session agent, not a user proxy — denied on groundwater READ.
    weak = AgentIdentity(name="weak_agent", trust_level=TrustLevel.SESSION)
    reader = open_vault_store(cfg, identity=weak, scope=AppScope(cross_app=True))
    try:
        writer.upsert(_droplet("g1", content="deep stuff", reservoir="groundwater"))

        got = reader.get("g1")
        assert got is None  # denied

        denied = reader.audit.query(actor="weak_agent", allowed=False)
        assert len(denied) >= 1
        assert any(e.operation == "read" and e.droplet_id == "g1" for e in denied)
        assert denied[-1].detail is not None  # denial reason recorded
    finally:
        writer.close()
        reader.close()


# ------------------------------------------------- search/rebuild under encryption
def test_search_similar_under_encryption(tmp_path):
    cfg = _config(tmp_path, key="encrypt-me")
    intel = build_intelligence(cfg)
    store = open_vault_store(cfg)
    try:
        texts = {
            "d1": "calendar meeting on tuesday afternoon",
            "d2": "calendar meeting reschedule tuesday",
            "d3": "grocery shopping list milk eggs",
        }
        for did, text in texts.items():
            d = _droplet(did, content=text)
            d.embedding = intel.embedder.embed(text)
            store.upsert(d)

        query_vec = intel.embedder.embed("calendar meeting tuesday")
        hits = store.search_similar(query_vec, k=3)
        ids = [did for did, _ in hits]

        # The two calendar droplets must rank above the grocery one.
        assert "d1" in ids and "d2" in ids
        assert ids.index("d1") < ids.index("d3")
        # Cosine scores are descending (ranking preserved under encryption).
        scores = [score for _, score in hits]
        assert scores == sorted(scores, reverse=True)
    finally:
        store.close()


def test_rebuild_index_under_encryption(tmp_path):
    cfg = _config(tmp_path, key="encrypt-me")
    intel = build_intelligence(cfg)
    store = open_vault_store(cfg)
    try:
        for did, text in {"d1": "ocean tides and salt water", "d2": "mountain snow peaks"}.items():
            d = _droplet(did, content=text)
            d.embedding = intel.embedder.embed(text)
            store.upsert(d)

        store.rebuild_index()  # rebuild from stored (plaintext-in-index) embeddings
        hits = store.search_similar(intel.embedder.embed("ocean salt tides"), k=2)
        assert hits[0][0] == "d1"
        assert hits[0][1] > hits[1][1]
    finally:
        store.close()


def test_search_similar_respects_app_scope(tmp_path):
    cfg = _config(tmp_path, key="k")
    intel = build_intelligence(cfg)
    store_a = open_vault_store(cfg, scope=AppScope(app_id="a"))
    store_b = open_vault_store(cfg, scope=AppScope(app_id="b"))
    try:
        text = "shared topic about project planning"
        da = _droplet("a1", content=text)
        da.embedding = intel.embedder.embed(text)
        store_a.upsert(da)

        # store_b searches with the same vector but must not see app a's droplet.
        hits_b = store_b.search_similar(intel.embedder.embed(text), k=5)
        assert all(did != "a1" for did, _ in hits_b)
    finally:
        store_a.close()
        store_b.close()


# ------------------------------------------------------------- cycle + delete
def test_touch_cycle_preserves_encryption_and_content(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    try:
        store.upsert(_droplet("m1", content="cycling content"))
        store.touch_cycle("m1", increment_count=True)
        got = store.get("m1")
        assert got is not None
        assert got.cycle.cycle_count == 1
        assert got.content == "cycling content"  # still decryptable

        # Still ciphertext on disk after the cycle update.
        row = _raw_row(cfg.db_path, "m1")
        assert "cycling content" not in row["content"]
    finally:
        store.close()


def test_delete_removes_droplet(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    try:
        store.upsert(_droplet("m1", content="to delete"))
        assert store.get("m1") is not None
        store.delete("m1")
        assert store.get("m1") is None
    finally:
        store.close()


# ------------------------------------------------------------- engine wiring
def test_build_vault_engine_absorb_and_recall(tmp_path):
    cfg = _config(tmp_path, key="engine-key")
    engine = build_vault_engine(cfg, app_id="calendar")
    try:
        from hydromemory.vault.vault import VaultRepository

        assert isinstance(engine.repo, VaultRepository)
        assert engine.repo.scope.app_id == "calendar"

        result = engine.absorb("User prefers morning meetings.", source="conversation")
        assert result is not None

        # The stored droplet is encrypted at rest but recoverable through the engine.
        ids = engine.repo.all_ids()
        assert len(ids) >= 1
        row = _raw_row(cfg.db_path, ids[0])
        assert "morning meetings" not in (row["content"] or "")
        assert row["app_id"] == "calendar"
    finally:
        engine.close()


def test_build_vault_engine_defaults_to_cross_app(tmp_path):
    cfg = _config(tmp_path, key="k")
    engine = build_vault_engine(cfg)  # no app_id -> owner L2 vault
    try:
        assert engine.repo.scope.cross_app is True
        assert engine.repo.scope.app_id is None
    finally:
        engine.close()


def test_open_vault_store_default_identity_is_user_proxy(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    try:
        assert store.identity.is_user_proxy is True
        assert isinstance(store.context, AccessContext)
    finally:
        store.close()


def test_audit_log_constructs_table_on_bare_connection(tmp_path):
    # AuditLog must create its own table idempotently on any sqlite connection.
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    conn.row_factory = sqlite3.Row
    try:
        log = AuditLog(conn)
        assert log.verify_chain() is True  # empty chain verifies
        assert log.query() == []
    finally:
        conn.close()
