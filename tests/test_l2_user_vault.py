"""L2 — User Memory Vault: the owner's cross-app view aggregates all apps.

Scenario (same SHARED backing as L1): apps "a" and "b" each write a droplet
through their scoped views; an *owner* :class:`~hydromemory.vault.vault.VaultRepository`
view — ``AppScope(cross_app=True)`` under a user-proxy HIGH_TRUST identity —
sees BOTH apps' droplets via ``query`` / ``all_ids`` / ``get``.

This is the L2 guarantee: while each app is isolated to its own scope (L1), the
owner operating their own vault crosses every app scope and reads the union,
still subject to governance (the user-proxy clears every reservoir's trust
floor + per-droplet allow-lists).
"""
from __future__ import annotations

import pytest

from hydromemory.config import HydroConfig
from hydromemory.platform.runtime import build_app_views
from hydromemory.schema import Droplet
from hydromemory.storage.sqlite_repository import SqliteDropletRepository
from hydromemory.vault.audit import AuditLog
from hydromemory.vault.cipher import build_cipher


def _droplet(did: str, *, content: str, reservoir: str = "surface") -> Droplet:
    return Droplet.from_dict(
        {
            "id": did,
            "content": content,
            "reservoir": reservoir,
            "state": {"purity": 0.9, "confidence": 0.9},
        }
    )


@pytest.fixture
def shared(tmp_path):
    cfg = HydroConfig(
        db_path=str(tmp_path / "l2.db"),
        vector_dim=64,
        intelligence_backend="stub",
        vault_key="l2-secret",
    )
    backing = SqliteDropletRepository(cfg)
    # Pass the backing connection so the passphrase KDF salt is persisted in the
    # SAME vault DB (scrypt over a per-vault salt; see vault/cipher.py).
    cipher = build_cipher(cfg, conn=backing._conn)
    audit = AuditLog(backing._conn)
    app_views, owner_view = build_app_views(
        backing, cipher, audit, app_ids=["a", "b"]
    )
    try:
        yield app_views, owner_view, cfg
    finally:
        backing.close()


def test_l2_owner_sees_across_all_app_scopes(shared):
    app_views, owner, _cfg = shared
    app_views["a"].upsert(_droplet("a1", content="calendar event next tuesday"))
    app_views["b"].upsert(_droplet("b1", content="grocery list milk and eggs"))

    # Owner (cross_app=True, user-proxy) sees BOTH apps' droplets.
    assert set(owner.all_ids()) == {"a1", "b1"}

    by_id = {d.id: d for d in owner.query()}
    assert set(by_id) == {"a1", "b1"}
    # Content decrypts for the owner across scopes.
    assert by_id["a1"].content == "calendar event next tuesday"
    assert by_id["b1"].content == "grocery list milk and eggs"

    # Direct cross-scope get() works for the owner where it was denied per-app.
    assert owner.get("a1") is not None
    assert owner.get("b1") is not None


def test_l2_owner_reads_higher_trust_reservoirs_apps_wrote(shared):
    """The owner crosses scope AND clears trust floors an app cannot."""
    app_views, owner, _cfg = shared
    # App "a" (a user-proxy app view) parks a groundwater + a sacred droplet.
    app_views["a"].upsert(_droplet("a_g", content="identity pattern", reservoir="groundwater"))
    app_views["b"].upsert(_droplet("b_s", content="a core vow", reservoir="sacred"))

    ids = set(owner.all_ids())
    assert {"a_g", "b_s"} <= ids
    # The user-proxy owner reads both restricted reservoirs across scopes.
    assert owner.get("a_g") is not None
    assert owner.get("b_s") is not None


def test_l2_owner_writes_are_visible_to_the_owning_app_scope(shared):
    """A shared backing means an owner-tagged write is queryable too.

    The owner view is cross-app, so its writes are not stamped with an app_id;
    they remain visible to the owner (cross_app sees all) even though no single
    app scope claims them — demonstrating the shared-backing wiring.
    """
    app_views, owner, _cfg = shared
    app_views["a"].upsert(_droplet("a1", content="app a"))
    owner.upsert(_droplet("owner1", content="owner-level note"))

    # Owner sees its own write + the app's write.
    assert {"a1", "owner1"} <= set(owner.all_ids())
    # App "a" sees only its own scoped droplet (the owner write has no app_id).
    assert app_views["a"].all_ids() == ["a1"]
