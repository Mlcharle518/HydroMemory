"""CLI tests for the vault key-management commands (`vault-rotate`, `vault-encrypt`).

Keys are read from the environment (`HYDRO_VAULT_KEY` / `HYDRO_VAULT_PREV_KEYS`),
never from argv — these tests set them via ``monkeypatch.setenv`` and drive
``hydromemory.cli.main`` directly, asserting the on-disk effect through a vault store.
"""
from __future__ import annotations

from hydromemory.cli import main
from hydromemory.config import HydroConfig
from hydromemory.schema import Droplet
from hydromemory.vault import open_vault_store


def _store(db: str, *, key: str | None):
    # vector_dim left at the default (256) to match the CLI's env-derived config,
    # so the shared .vec.npz dimension is consistent across seeding + the CLI op.
    return open_vault_store(HydroConfig(db_path=db, vault_key=key))


def _seed(store, did: str, content: str) -> None:
    store.upsert(Droplet.from_dict({"id": did, "content": content, "state": {"purity": 0.9}}))


def test_cli_vault_encrypt_migrates_keyless(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.db")
    s0 = _store(db, key=None)  # keyless -> plaintext on disk
    _seed(s0, "m1", "cli plaintext secret")
    s0.close()

    monkeypatch.setenv("HYDRO_VAULT_KEY", "cli-key")
    monkeypatch.delenv("HYDRO_VAULT_PREV_KEYS", raising=False)
    rc = main(["--db", db, "vault-encrypt"])
    assert rc == 0
    assert "encrypted 1" in capsys.readouterr().out

    s1 = _store(db, key="cli-key")
    try:
        assert s1.get("m1").content == "cli plaintext secret"  # decrypts under the key
    finally:
        s1.close()


def test_cli_vault_encrypt_without_key_errors(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.db")
    _store(db, key=None).close()
    monkeypatch.delenv("HYDRO_VAULT_KEY", raising=False)
    rc = main(["--db", db, "vault-encrypt"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cli_absorb_is_vault_backed_when_keyed(tmp_path, monkeypatch, capsys):
    import sqlite3

    db = str(tmp_path / "cli.db")
    monkeypatch.setenv("HYDRO_VAULT_KEY", "cli-key")
    monkeypatch.delenv("HYDRO_VAULT_PREV_KEYS", raising=False)

    rc = main(["--db", db, "absorb", "--content", "vault-backed cli note", "--source", "conversation"])
    assert rc == 0

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT content FROM droplets").fetchall()
    finally:
        conn.close()
    assert rows  # something was stored
    assert all("vault-backed cli note" not in (r[0] or "") for r in rows)  # ciphertext at rest


def test_cli_vault_rotate(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.db")
    s0 = _store(db, key="key-one")
    _seed(s0, "m1", "rotate me via cli")
    s0.close()

    monkeypatch.setenv("HYDRO_VAULT_KEY", "key-two")
    monkeypatch.setenv("HYDRO_VAULT_PREV_KEYS", "key-one")
    rc = main(["--db", db, "vault-rotate"])
    assert rc == 0
    assert "rotated 1" in capsys.readouterr().out

    # key-two alone now reads it (rotation persisted to the new primary).
    s1 = _store(db, key="key-two")
    try:
        assert s1.get("m1").content == "rotate me via cli"
    finally:
        s1.close()
