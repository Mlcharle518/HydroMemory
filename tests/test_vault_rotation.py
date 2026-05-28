"""Vault key-rotation + re-encryption tests (v2 hardening).

Covers the cipher layer (MultiFernet primary + retired keys, token-level
``rotate``) and the end-to-end ``VaultRepository.rotate_keys`` migration:
rows written under an old key are re-encrypted to the new primary, after which
the old key alone can no longer read them — while embeddings, search, and the
audit chain survive. Rotation is owner-only and a no-op under NullCipher. Also
covers the one-time keyless -> encrypted migration (``encrypt_plaintext_rows``).

These mirror ``test_v2_vault.py`` and use arbitrary passphrases as keys (a
passphrase is stretched with scrypt over a per-vault salt persisted in
``vault_meta``; the legacy unsalted sha256 key is kept as a decrypt fallback), so
``cryptography`` — already a confirmed dependency of the vault test suite — is the
only requirement.
"""
from __future__ import annotations

import sqlite3

import pytest

from hydromemory.config import HydroConfig
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.intelligence import build_intelligence
from hydromemory.schema import Droplet
from hydromemory.storage.sqlite_repository import SqliteDropletRepository
from hydromemory.vault import AppScope, encrypt_vault, open_vault_store
from hydromemory.vault.cipher import FernetCipher, NullCipher, build_cipher


# --------------------------------------------------------------------- helpers
def _config(tmp_path, *, key: str | None = None, prev: tuple[str, ...] = ()) -> HydroConfig:
    return HydroConfig(
        db_path=str(tmp_path / "vault.db"),
        vector_dim=64,
        intelligence_backend="stub",
        vault_key=key,
        vault_prev_keys=list(prev),
    )


def _droplet(did: str, *, content: str, reservoir: str = "surface", **state) -> Droplet:
    return Droplet.from_dict(
        {"id": did, "content": content, "reservoir": reservoir, "state": {"purity": 0.9, **state}}
    )


def _raw_content(db_path: str, did: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT content FROM droplets WHERE id = ?", (did,)).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


# ----------------------------------------------------------------- cipher layer
def test_fernet_multikey_decrypts_previous_and_rotates_to_primary():
    from cryptography.fernet import InvalidToken

    old = FernetCipher("old-key-1")
    token = old.encrypt(b"secret payload")

    # New cipher holds the new primary plus the retired key -> decrypts old rows.
    multi = FernetCipher("new-key-2", previous_keys=["old-key-1"])
    assert multi.decrypt(token) == b"secret payload"

    # rotate() re-encrypts to the primary: readable under new-only, NOT old-only.
    rotated = multi.rotate(token)
    assert FernetCipher("new-key-2").decrypt(rotated) == b"secret payload"
    with pytest.raises(InvalidToken):
        old.decrypt(rotated)


def test_nullcipher_rotate_is_identity():
    assert NullCipher().rotate(b"abc") == b"abc"


def test_build_cipher_threads_previous_keys(tmp_path):
    # db_path is real so the per-vault scrypt salt persists in vault_meta (passing
    # the connection keeps it hermetic — no stray default DB in the cwd).
    cfg = HydroConfig(db_path=str(tmp_path / "v.db"), vault_key="new", vault_prev_keys=["old"])
    backing = SqliteDropletRepository(cfg)
    try:
        cipher = build_cipher(cfg, conn=backing._conn)
        # A row written under the retired passphrase via the LEGACY unsalted
        # sha256 derivation (no salt -> FernetCipher falls back to sha256) still
        # decrypts: build_cipher keeps each passphrase's sha256 key as a fallback.
        token = FernetCipher("old").encrypt(b"x")
        assert cipher.decrypt(token) == b"x"
    finally:
        backing.close()


# ------------------------------------------------------------- end-to-end rotate
def test_rotate_keys_reencrypts_all_rows(tmp_path):
    from cryptography.fernet import InvalidToken

    # Write two rows under key-one.
    s1 = open_vault_store(_config(tmp_path, key="key-one"))
    s1.upsert(_droplet("m1", content="rotate me one"))
    s1.upsert(_droplet("m2", content="rotate me two"))
    before = _raw_content(s1.backing.db_path, "m1")
    s1.close()

    # Reopen with primary key-two + retired key-one: reads still work pre-rotation.
    s2 = open_vault_store(_config(tmp_path, key="key-two", prev=("key-one",)))
    assert s2.get("m1").content == "rotate me one"  # decrypted via the retired key
    assert s2.rotate_keys() == 2
    assert _raw_content(s2.backing.db_path, "m1") != before  # ciphertext changed
    assert s2.audit.verify_chain() is True
    assert any(e.operation == "rotate_keys" and e.allowed for e in s2.audit.query())
    s2.close()

    # Only key-two now: both rows decrypt -> rotation persisted to the new key.
    s3 = open_vault_store(_config(tmp_path, key="key-two"))
    assert s3.get("m1").content == "rotate me one"
    assert s3.get("m2").content == "rotate me two"
    s3.close()

    # Only key-one can no longer read them (the ciphertext key really changed).
    s4 = open_vault_store(_config(tmp_path, key="key-one"))
    with pytest.raises(InvalidToken):
        s4.get("m1")
    s4.close()


def test_rotate_keys_is_idempotent(tmp_path):
    s1 = open_vault_store(_config(tmp_path, key="k1"))
    s1.upsert(_droplet("m1", content="once"))
    s1.close()

    s2 = open_vault_store(_config(tmp_path, key="k2", prev=("k1",)))
    try:
        assert s2.rotate_keys() == 1  # k1 -> k2
        # Re-running rotates k2 -> k2 again; still safe, content intact.
        assert s2.rotate_keys() == 1
        assert s2.get("m1").content == "once"
    finally:
        s2.close()


def test_rotate_keys_preserves_embeddings_and_search(tmp_path):
    cfg1 = _config(tmp_path, key="k1")
    intel = build_intelligence(cfg1)
    s1 = open_vault_store(cfg1)
    for did, text in {"d1": "ocean salt tides", "d2": "mountain snow peaks"}.items():
        d = _droplet(did, content=text)
        d.embedding = intel.embedder.embed(text)
        s1.upsert(d)
    s1.close()

    s2 = open_vault_store(_config(tmp_path, key="k2", prev=("k1",)))
    try:
        assert s2.rotate_keys() == 2
        hits = s2.search_similar(intel.embedder.embed("ocean tides salt"), k=2)
        assert hits[0][0] == "d1"  # vector ranking survived rotation
        assert s2.get("d1").content == "ocean salt tides"  # decrypts under the new key
    finally:
        s2.close()


def test_rotate_keys_noop_under_null_cipher(tmp_path):
    store = open_vault_store(_config(tmp_path, key=None))  # NullCipher (keyless dev)
    try:
        store.upsert(_droplet("m1", content="plain"))
        assert store.rotate_keys() == 0  # nothing to re-key
        assert store.get("m1").content == "plain"
    finally:
        store.close()


def test_rotate_keys_requires_owner_identity(tmp_path):
    cfg = _config(tmp_path, key="k")
    owner = open_vault_store(cfg)
    owner.upsert(_droplet("m1", content="x"))
    owner.close()

    weak = AgentIdentity(name="weak", trust_level=TrustLevel.SESSION)  # not a user proxy
    store = open_vault_store(cfg, identity=weak, scope=AppScope(cross_app=True))
    try:
        with pytest.raises(PermissionError):
            store.rotate_keys()
        denied = store.audit.query(actor="weak", operation="rotate_keys", allowed=False)
        assert len(denied) >= 1
    finally:
        store.close()


# ----------------------------------------------- keyless -> encrypted migration
def test_encrypt_plaintext_rows_migrates_keyless_vault(tmp_path):
    from cryptography.fernet import InvalidToken

    # Write with NO key -> NullCipher -> plaintext on disk.
    s0 = open_vault_store(_config(tmp_path, key=None))
    s0.upsert(_droplet("m1", content="was plaintext one"))
    s0.upsert(_droplet("m2", content="was plaintext two"))
    assert "was plaintext one" in _raw_content(s0.backing.db_path, "m1")
    s0.close()

    # Reopen WITH a key: the old plaintext rows cannot be Fernet-decrypted yet.
    s1 = open_vault_store(_config(tmp_path, key="new-key"))
    with pytest.raises(InvalidToken):
        s1.get("m1")
    # Migrate: encrypt the plaintext rows in place.
    assert s1.encrypt_plaintext_rows() == 2
    assert "was plaintext one" not in _raw_content(s1.backing.db_path, "m1")  # ciphertext now
    assert s1.get("m1").content == "was plaintext one"  # decrypts under the key
    assert s1.get("m2").content == "was plaintext two"
    assert s1.audit.verify_chain() is True
    assert any(e.operation == "encrypt_plaintext_rows" and e.allowed for e in s1.audit.query())
    s1.close()


def test_encrypt_plaintext_rows_is_idempotent(tmp_path):
    s0 = open_vault_store(_config(tmp_path, key=None))
    s0.upsert(_droplet("m1", content="plain"))
    s0.close()

    s1 = open_vault_store(_config(tmp_path, key="k"))
    try:
        assert s1.encrypt_plaintext_rows() == 1  # migrated
        assert s1.encrypt_plaintext_rows() == 0  # nothing left to encrypt
        assert s1.get("m1").content == "plain"
    finally:
        s1.close()


def test_encrypt_plaintext_rows_requires_key(tmp_path):
    store = open_vault_store(_config(tmp_path, key=None))  # NullCipher, no key
    try:
        store.upsert(_droplet("m1", content="plain"))
        with pytest.raises(RuntimeError):
            store.encrypt_plaintext_rows()
    finally:
        store.close()


def test_encrypt_plaintext_rows_requires_owner(tmp_path):
    cfg = _config(tmp_path, key="k")
    owner = open_vault_store(cfg)
    owner.upsert(_droplet("m1", content="x"))
    owner.close()

    weak = AgentIdentity(name="weak", trust_level=TrustLevel.SESSION)
    store = open_vault_store(cfg, identity=weak, scope=AppScope(cross_app=True))
    try:
        with pytest.raises(PermissionError):
            store.encrypt_plaintext_rows()
        assert store.audit.query(actor="weak", operation="encrypt_plaintext_rows", allowed=False)
    finally:
        store.close()


def test_encrypt_vault_convenience(tmp_path):
    s0 = open_vault_store(_config(tmp_path, key=None))
    s0.upsert(_droplet("m1", content="convenience plain"))
    s0.close()

    assert encrypt_vault(_config(tmp_path, key="k")) == 1
    s1 = open_vault_store(_config(tmp_path, key="k"))
    try:
        assert s1.get("m1").content == "convenience plain"
    finally:
        s1.close()
