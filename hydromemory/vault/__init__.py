"""User-Controlled Memory Vault (PRD §9, v2): encrypted, audited, app-scoped.

Phase A0 ships the contract (Cipher, AuditLog, AppScope, VaultRepository) plus
the factory signatures. Phase B1 implements the encrypt/decrypt + audit + scope
behavior and the `open_vault_store` / `build_vault_engine` wiring.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from hydromemory.config import HydroConfig
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.vault.audit import AUDIT_DDL, AuditEntry, AuditLog
from hydromemory.vault.cipher import Cipher, FernetCipher, NullCipher, build_cipher
from hydromemory.vault.scope import AppIdentity, AppScope
from hydromemory.vault.vault import VaultRepository

if TYPE_CHECKING:
    from hydromemory.engine import Engine

__all__ = [
    "Cipher",
    "NullCipher",
    "FernetCipher",
    "build_cipher",
    "AuditEntry",
    "AuditLog",
    "AUDIT_DDL",
    "AppIdentity",
    "AppScope",
    "VaultRepository",
    "open_vault_store",
    "build_vault_engine",
    "rotate_vault_keys",
    "encrypt_vault",
]


def _default_identity() -> AgentIdentity:
    """A user-proxy identity (the owner acting directly on their own vault)."""
    return AgentIdentity(
        name="user",
        trust_level=TrustLevel.HIGH_TRUST,
        is_user_proxy=True,
    )


def _default_scope(config: HydroConfig, app_id: str | None) -> AppScope:
    """L1 app scope when an ``app_id`` is given, else the L2 owner (cross-app) vault."""
    chosen = app_id if app_id is not None else config.app_id
    if chosen:
        return AppScope(app_id=chosen)
    return AppScope(cross_app=True)


def open_vault_store(
    config: HydroConfig,
    *,
    identity: AgentIdentity | None = None,
    scope: AppScope | None = None,
) -> VaultRepository:
    """Open a :class:`VaultRepository` over the configured store.

    Wraps a fresh ``SqliteDropletRepository`` with the configured cipher
    (:func:`build_cipher`) and an :class:`AuditLog` on the *same* connection, so
    audit rows live alongside the droplets. Defaults: a user-proxy ``identity``
    and — absent an explicit ``scope`` — an L1 app scope when ``config.app_id`` is
    set, else the L2 owner (cross-app) vault.
    """
    from hydromemory.storage import SqliteDropletRepository

    backing = SqliteDropletRepository(config)
    cipher = build_cipher(config)
    audit = AuditLog(backing._conn)
    return VaultRepository(
        backing,
        cipher,
        audit,
        identity=identity or _default_identity(),
        scope=scope or _default_scope(config, config.app_id),
    )


def build_vault_engine(
    config: HydroConfig,
    *,
    app_id: str | None = None,
    identity: AgentIdentity | None = None,
) -> Engine:
    """Build an :class:`~hydromemory.engine.Engine` whose repo is a scoped vault.

    Reuses the v1 engine wiring (intelligence + the full :class:`Verbs` bundle)
    but injects a :class:`VaultRepository` as the repo, so every engine/verb
    operation is encrypted, audited, access-enforced, and app-scoped (L1 when
    ``app_id`` is given, else the owner's L2 cross-app vault).
    """
    from hydromemory import contamination as contamination_mod
    from hydromemory import forgetting as forgetting_mod
    from hydromemory.engine import Engine
    from hydromemory.governance import check_access, permission_score, privacy_risk
    from hydromemory.intelligence import build_intelligence
    from hydromemory.verbs import Verbs

    repo = open_vault_store(
        config,
        identity=identity or _default_identity(),
        scope=_default_scope(config, app_id),
    )
    intelligence = build_intelligence(config)
    verbs = Verbs(
        repo=repo,
        intelligence=intelligence,
        check_access=check_access,
        forgetting=forgetting_mod,
        contamination=contamination_mod,
        permission_score=permission_score,
        privacy_risk=privacy_risk,
    )
    return Engine(config=config, repo=repo, intelligence=intelligence, verbs=verbs)


def rotate_vault_keys(config: HydroConfig, *, identity: AgentIdentity | None = None) -> int:
    """Re-encrypt the whole vault to ``config.vault_key`` (the new primary).

    Set ``HYDRO_VAULT_KEY`` to the new key and ``HYDRO_VAULT_PREV_KEYS`` to the
    retired one(s), then call this once: it opens the owner's cross-app vault and
    re-encrypts every droplet to the primary, after which the retired keys can be
    dropped. Returns the number of rows re-encrypted. Owner-only (user-proxy).
    """
    store = open_vault_store(
        config,
        identity=identity or _default_identity(),
        scope=AppScope(cross_app=True),
    )
    try:
        return store.rotate_keys()
    finally:
        store.close()


def encrypt_vault(config: HydroConfig, *, identity: AgentIdentity | None = None) -> int:
    """Encrypt a previously **keyless** (plaintext) vault under ``config.vault_key``.

    The one-time keyless -> encrypted migration. Set ``HYDRO_VAULT_KEY`` to the new
    key, then call this once: it opens the owner's cross-app vault and Fernet-encrypts
    every still-plaintext row in place. Returns the number of rows encrypted.
    Owner-only; idempotent (already-encrypted rows are skipped). Distinct from
    :func:`rotate_vault_keys`, which rotates between Fernet keys.
    """
    store = open_vault_store(
        config,
        identity=identity or _default_identity(),
        scope=AppScope(cross_app=True),
    )
    try:
        return store.encrypt_plaintext_rows()
    finally:
        store.close()
