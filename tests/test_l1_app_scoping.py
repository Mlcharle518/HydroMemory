"""L1 — App-scoped memory isolation over a SHARED vault backing (v2 Phase B2).

Scenario: two apps ("a" and "b") write into the same user vault through
per-app :class:`~hydromemory.vault.vault.VaultRepository` views built over ONE
shared :class:`~hydromemory.storage.sqlite_repository.SqliteDropletRepository`
backing (so the vector index + on-disk rows are shared, not split across
connections — see :func:`hydromemory.platform.runtime.build_app_views`).

Asserts the L1 guarantee: each app sees ONLY its own droplets via ``query`` /
``all_ids`` / ``get``; a cross-app ``get`` is denied (returns ``None``). Scope
assertions use ``query``/``get`` — not vector search — so the result never
depends on index-staleness.

NOTE: ``VaultRepository`` checks ``_in_scope`` *first* on the single-id methods
(``get``/``delete``/``touch_cycle``/``add_link``/``remove_link``). A cross-app
attempt still returns ``None`` / no-ops, but the *attempt* is now audited as a
denied entry (reason "out of app scope"), closing the gap ADR-0021 first noted —
so cross-scope probes are visible in the owner's tamper-evident log. (Bulk
``query`` / ``search_similar`` scope-filtering stays a silent per-row filter, not
a targeted probe.) The in-scope ``check_access`` denial path is also audited
(asserted via a weak, non-user-proxy identity reading a higher-trust reservoir
below).
"""
from __future__ import annotations

import pytest

from hydromemory.config import HydroConfig
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.platform.runtime import build_app_views
from hydromemory.schema import Droplet
from hydromemory.storage.sqlite_repository import SqliteDropletRepository
from hydromemory.vault.audit import AuditLog
from hydromemory.vault.cipher import build_cipher
from hydromemory.vault.scope import AppScope
from hydromemory.vault.vault import VaultRepository


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
    """One shared backing + cipher + audit; per-app + owner vault views."""
    cfg = HydroConfig(
        db_path=str(tmp_path / "l1.db"),
        vector_dim=64,
        intelligence_backend="stub",
        vault_key="l1-secret",
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
        yield {
            "cfg": cfg,
            "backing": backing,
            "cipher": cipher,
            "audit": audit,
            "app_views": app_views,
            "owner_view": owner_view,
        }
    finally:
        backing.close()


def test_l1_apps_share_one_backing_but_see_only_their_own(shared):
    app_a = shared["app_views"]["a"]
    app_b = shared["app_views"]["b"]

    app_a.upsert(_droplet("a1", content="app a private note"))
    app_b.upsert(_droplet("b1", content="app b private note"))

    # all_ids: each app sees only its own id (shared backing, scoped view).
    assert app_a.all_ids() == ["a1"]
    assert app_b.all_ids() == ["b1"]

    # query(): each app's scope filter returns only its own droplet.
    assert [d.id for d in app_a.query()] == ["a1"]
    assert [d.id for d in app_b.query()] == ["b1"]

    # get() of the OTHER app's droplet is out-of-scope -> denied (None).
    assert app_a.get("b1") is None
    assert app_b.get("a1") is None

    # The in-scope reads round-trip (content decrypts correctly).
    got_a = app_a.get("a1")
    assert got_a is not None and got_a.content == "app a private note"


def test_l1_out_of_scope_attempt_is_audited(shared):
    """A cross-app (out-of-scope) get is recorded as a denied attempt (ADR-0021 gap closed)."""
    app_a = shared["app_views"]["a"]
    app_b = shared["app_views"]["b"]
    app_b.upsert(_droplet("b1", content="app b private note"))

    assert app_a.get("b1") is None  # still isolated...

    # ...but the attempt now appears in the shared tamper-evident audit log.
    denied = app_a.audit.query(allowed=False)
    assert any(
        e.operation == "read" and e.droplet_id == "b1" and e.detail == "out of app scope"
        for e in denied
    )
    assert app_a.audit.verify_chain() is True


def test_l1_in_scope_denied_read_is_audited(shared):
    """An in-scope read refused by ``check_access`` is recorded (denial path)."""
    backing = shared["backing"]
    cipher = shared["cipher"]
    audit = shared["audit"]

    # App "a" (user-proxy) writes a high-trust groundwater droplet into scope a.
    app_a = shared["app_views"]["a"]
    app_a.upsert(_droplet("a_deep", content="deep identity pattern", reservoir="groundwater"))

    # A weak (session-trust, non-user-proxy) app view over the SAME scope "a":
    # the id is in scope, so it clears the scope filter and reaches the access
    # gate, which denies it (groundwater requires high trust) and audits it.
    weak = AgentIdentity(name="weak_app", trust_level=TrustLevel.SESSION)
    weak_a = VaultRepository(
        backing, cipher, audit, identity=weak, scope=AppScope(app_id="a")
    )
    assert weak_a.get("a_deep") is None  # denied

    denied = weak_a.audit.query(actor="weak_app", allowed=False)
    assert any(e.operation == "read" and e.droplet_id == "a_deep" for e in denied)
    assert denied[-1].detail is not None  # the denial reason is recorded


def test_l1_audit_chain_stays_verifiable_across_shared_writes(shared):
    app_a = shared["app_views"]["a"]
    app_b = shared["app_views"]["b"]

    app_a.upsert(_droplet("a1", content="one"))
    app_b.upsert(_droplet("b1", content="two"))
    app_a.get("a1")
    app_b.query()

    # All writes + reads extend a single tamper-evident chain on the shared conn.
    assert app_a.audit.verify_chain() is True
