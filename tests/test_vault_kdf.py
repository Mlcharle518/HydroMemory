"""Vault passphrase KDF + audit tail-truncation tests (H3, M1 hardening).

H3 — passphrase key derivation. A non-Fernet ``vault_key`` is no longer an
unsalted single ``sha256``; it is stretched with ``scrypt`` over a random,
per-vault 16-byte salt persisted in the ``vault_meta`` table. These tests prove:

* a passphrase derives its key via scrypt with a salt persisted in ``vault_meta``
  (the derived key is NOT the legacy ``sha256`` key);
* the SAME vault round-trips content (scrypt is deterministic given salt+pass);
* two DIFFERENT vault DBs with the SAME passphrase get DIFFERENT salts/keys
  (so a token from one does not decrypt in the other);
* a raw urlsafe-base64 Fernet key keeps the unchanged fast path (used verbatim,
  no salt written);
* data written under the LEGACY unsalted ``sha256`` key still decrypts after the
  upgrade (MultiFernet fallback), and ``rotate_keys`` then migrates it to the
  scrypt primary so the legacy key alone can no longer read it.

M1 — audit tail truncation. Lopping the last row off the chain used to still
``verify_chain`` cleanly; a persisted head watermark now makes it fail.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3

import pytest

from hydromemory.config import HydroConfig
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.schema import Droplet
from hydromemory.storage.sqlite_repository import SqliteDropletRepository
from hydromemory.vault import AppScope, open_vault_store
from hydromemory.vault.audit import AuditLog
from hydromemory.vault.cipher import FernetCipher
from hydromemory.vault.vault import VaultRepository


# --------------------------------------------------------------------- helpers
def _config(tmp_path, name: str = "vault.db", *, key: str | None = None) -> HydroConfig:
    return HydroConfig(
        db_path=str(tmp_path / name),
        vector_dim=64,
        intelligence_backend="stub",
        vault_key=key,
    )


def _droplet(did: str, *, content: str, reservoir: str = "surface", **state) -> Droplet:
    return Droplet.from_dict(
        {"id": did, "content": content, "reservoir": reservoir, "state": {"purity": 0.9, **state}}
    )


def _meta_value(db_path: str, name: str) -> bytes | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT value FROM vault_meta WHERE name = ?", (name,)).fetchone()
        return None if row is None else bytes(row["value"])
    finally:
        conn.close()


def _legacy_sha256_key(passphrase: str) -> bytes:
    """The OLD (pre-scrypt) derivation: base64(sha256(passphrase))."""
    return base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode()).digest())


def _owner() -> AgentIdentity:
    return AgentIdentity(name="user", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True)


# --------------------------------------------------------- passphrase -> scrypt
def test_passphrase_derives_scrypt_key_with_persisted_salt(tmp_path):
    cfg = _config(tmp_path, key="a-human-passphrase")
    store = open_vault_store(cfg)
    try:
        # A 16-byte salt was generated and persisted in vault_meta on first use.
        salt = _meta_value(cfg.db_path, "kdf_salt")
        assert salt is not None
        assert len(salt) == 16

        # The cipher does NOT use the legacy unsalted sha256 key as its primary:
        # a token it writes is NOT decryptable by a legacy-only sha256 Fernet.
        from cryptography.fernet import Fernet, InvalidToken

        token = store.cipher.encrypt(b"probe")
        legacy_only = Fernet(_legacy_sha256_key("a-human-passphrase"))
        with pytest.raises(InvalidToken):
            legacy_only.decrypt(token)

        # But a scrypt key derived from the SAME salt+passphrase decrypts it,
        # proving the primary is the salted scrypt key.
        scrypt_key = FernetCipher._scrypt_key(b"a-human-passphrase", salt)
        assert Fernet(scrypt_key).decrypt(token) == b"probe"
    finally:
        store.close()


def test_same_vault_roundtrips_passphrase_content(tmp_path):
    cfg = _config(tmp_path, key="round-trip-pass")
    store = open_vault_store(cfg)
    try:
        store.upsert(_droplet("m1", content="scrypt protected content"))
        got = store.get("m1")
        assert got is not None and got.content == "scrypt protected content"
    finally:
        store.close()

    # Reopen the SAME vault DB: the persisted salt re-derives the SAME key, so
    # the previously written row still decrypts (deterministic given salt+pass).
    store2 = open_vault_store(_config(tmp_path, key="round-trip-pass"))
    try:
        assert store2.get("m1").content == "scrypt protected content"
    finally:
        store2.close()


def test_two_vaults_same_passphrase_get_different_salts_and_keys(tmp_path):
    from cryptography.fernet import InvalidToken

    cfg_a = _config(tmp_path, "a.db", key="shared-passphrase")
    cfg_b = _config(tmp_path, "b.db", key="shared-passphrase")
    store_a = open_vault_store(cfg_a)
    store_b = open_vault_store(cfg_b)
    try:
        store_a.upsert(_droplet("m1", content="only A can read this"))

        salt_a = _meta_value(cfg_a.db_path, "kdf_salt")
        salt_b = _meta_value(cfg_b.db_path, "kdf_salt")
        assert salt_a is not None and salt_b is not None
        assert salt_a != salt_b  # per-vault salt uniqueness

        # Same passphrase, different salt -> different key. A token encrypted in
        # vault A is NOT decryptable by vault B's cipher.
        token_a = store_a.cipher.encrypt(b"x")
        with pytest.raises(InvalidToken):
            store_b.cipher.decrypt(token_a)
    finally:
        store_a.close()
        store_b.close()


# --------------------------------------------------------- raw Fernet fast path
def test_raw_fernet_key_uses_fast_path_unchanged(tmp_path):
    from cryptography.fernet import Fernet

    raw_key = Fernet.generate_key().decode("ascii")  # a valid urlsafe-base64 key
    cfg = _config(tmp_path, key=raw_key)
    store = open_vault_store(cfg)
    try:
        # The raw key is used verbatim: a token written by the vault decrypts under
        # a plain Fernet(raw_key) — no KDF in between.
        store.upsert(_droplet("m1", content="raw fernet content"))
        token = store.cipher.encrypt(b"y")
        assert Fernet(raw_key.encode()).decrypt(token) == b"y"
        assert store.get("m1").content == "raw fernet content"

        # The fast path persists NO salt (no passphrase to stretch).
        assert _meta_value(cfg.db_path, "kdf_salt") is None
    finally:
        store.close()


# --------------------------------------------- legacy sha256 -> scrypt migration
def test_legacy_sha256_rows_decrypt_then_rotate_to_scrypt(tmp_path):
    from cryptography.fernet import InvalidToken

    cfg = _config(tmp_path, key="upgrade-me")

    # 1) Simulate a row written under the OLD scheme: a FernetCipher with NO salt
    #    falls back to the legacy unsalted sha256 derivation (exactly the pre-fix
    #    behavior). Write it through a VaultRepository so it lands like real data.
    backing = SqliteDropletRepository(cfg)
    legacy_cipher = FernetCipher("upgrade-me")  # salt=None -> sha256 primary
    audit = AuditLog(backing._conn)
    legacy_store = VaultRepository(
        backing, legacy_cipher, audit, identity=_owner(), scope=AppScope(cross_app=True)
    )
    legacy_store.upsert(_droplet("m1", content="written under legacy sha256"))
    backing.close()

    # 2) Reopen via the normal path: build_cipher now derives a scrypt primary but
    #    keeps the legacy sha256 key as a MultiFernet fallback, so the old row still
    #    reads.
    store = open_vault_store(cfg)
    try:
        assert store.get("m1").content == "written under legacy sha256"

        # 3) rotate_keys re-encrypts every row to the scrypt primary.
        assert store.rotate_keys() == 1
    finally:
        store.close()

    # 4) A legacy-sha256-only cipher can no longer read the rotated row (it is now
    #    under the salted scrypt key) — the migration really moved the ciphertext.
    backing2 = SqliteDropletRepository(cfg)
    try:
        row = backing2._conn.execute(
            "SELECT content FROM droplets WHERE id = ?", ("m1",)
        ).fetchone()
        from cryptography.fernet import Fernet

        legacy_only = Fernet(_legacy_sha256_key("upgrade-me"))
        with pytest.raises(InvalidToken):
            legacy_only.decrypt(row["content"].encode("ascii"))
    finally:
        backing2.close()

    # 5) ...but the normal (scrypt-primary + legacy-fallback) path still reads it.
    store2 = open_vault_store(cfg)
    try:
        assert store2.get("m1").content == "written under legacy sha256"
    finally:
        store2.close()


# ----------------------------------------------------- audit tail truncation (M1)
def test_audit_tail_truncation_is_detected(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    try:
        store.upsert(_droplet("m1", content="one"))
        store.upsert(_droplet("m2", content="two"))
        store.get("m1")
        store.get("m2")
        assert store.audit.verify_chain() is True

        conn = store.backing._conn
        # Delete the most recent audit row (tail truncation). The remaining chain
        # is internally consistent, but the head watermark is now ahead of it.
        last_seq = conn.execute("SELECT MAX(seq) AS m FROM audit").fetchone()["m"]
        conn.execute("DELETE FROM audit WHERE seq = ?", (last_seq,))
        conn.commit()

        assert store.audit.verify_chain() is False
    finally:
        store.close()


def test_audit_full_truncation_is_detected(tmp_path):
    cfg = _config(tmp_path, key="k")
    store = open_vault_store(cfg)
    try:
        store.upsert(_droplet("m1", content="one"))
        assert store.audit.verify_chain() is True

        conn = store.backing._conn
        conn.execute("DELETE FROM audit")  # wipe the whole tail
        conn.commit()
        # Watermark still records a head that no longer exists -> truncation caught.
        assert store.audit.verify_chain() is False
    finally:
        store.close()
