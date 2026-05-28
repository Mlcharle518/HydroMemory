"""Pluggable encryption for the User-Controlled Memory Vault (v2).

``build_cipher`` returns a real :class:`FernetCipher` when a key is configured,
else a labeled :class:`NullCipher` (plaintext) so offline tests/dev runs need no
secrets. ``cryptography`` is a lazy, optional dependency (the ``vault`` extra) —
imported only inside :class:`FernetCipher`, never at module load.

Key derivation. A configured key may be a **raw urlsafe-base64 Fernet key** (used
verbatim — the fast path) or an arbitrary **passphrase**. A passphrase is
stretched with ``scrypt`` over a random, per-vault 16-byte salt persisted in the
vault's ``vault_meta`` table (see :class:`~hydromemory.vault.audit.MetaStore`), so
identical passphrases on *different* vaults derive *different* keys and offline
brute-forcing is expensive. For backward compatibility the cipher also keeps the
**legacy** unsalted ``sha256(passphrase)`` key as a decrypt-only fallback (via
``MultiFernet``), so rows written before scrypt still read; ``rotate_keys`` then
re-encrypts them to the scrypt primary.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sqlite3
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from hydromemory.config import HydroConfig
from hydromemory.vault.audit import MetaStore

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# scrypt cost parameters. Moderate by design: strong enough to make offline
# brute-force of a human passphrase costly, but cheap enough (a few ms) that the
# test suite stays fast and deterministic. (n=2**14, r=8, p=1 ~= the interactive
# preset; raise n for production-grade secrets via a future config knob.)
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

# vault_meta key under which the per-vault passphrase salt is persisted.
_SALT_META_KEY = "kdf_salt"
_SALT_BYTES = 16


@runtime_checkable
class Cipher(Protocol):
    label: str

    def encrypt(self, data: bytes) -> bytes: ...

    def decrypt(self, token: bytes) -> bytes: ...

    def rotate(self, token: bytes) -> bytes:
        """Re-encrypt ``token`` under the current primary key (for key rotation)."""
        ...


class NullCipher:
    """Identity transform — stores plaintext. For offline/dev only; NOT secure."""

    label = "null-dev"

    def encrypt(self, data: bytes) -> bytes:
        return data

    def decrypt(self, token: bytes) -> bytes:
        return token

    def rotate(self, token: bytes) -> bytes:
        # Plaintext "tokens" have no key to rotate; rotation is a no-op.
        return token


class FernetCipher:
    """Real symmetric encryption via ``cryptography.fernet`` (lazy import).

    Holds a **primary** key plus any **retired** keys as a ``MultiFernet``:
    :meth:`encrypt` always uses the primary, :meth:`decrypt` tries every key (so
    rows written under an old key still decrypt during a rotation), and
    :meth:`rotate` re-encrypts a single token to the primary.

    Each key may be a raw urlsafe-base64 Fernet key (used as-is) or a passphrase.
    A passphrase is derived with ``scrypt`` over ``salt`` when one is supplied;
    its legacy unsalted ``sha256`` key is *also* added as a decrypt-only fallback
    so pre-scrypt rows still read. When ``salt`` is ``None`` (a bare construction
    with no vault to persist a salt into) a passphrase falls back to the legacy
    ``sha256`` derivation alone — preserving the original behavior.
    """

    label = "fernet"

    def __init__(
        self,
        key: str,
        *,
        previous_keys: Sequence[str] = (),
        salt: bytes | None = None,
    ) -> None:
        from cryptography.fernet import MultiFernet  # lazy, optional dependency

        self._salt = salt
        # Primary first (it encrypts), then its legacy fallback, then each retired
        # key's scrypt + legacy fallbacks (decrypt-only). Duplicates are harmless.
        fernets: list[Fernet] = []
        for is_primary, k in [(True, key), *[(False, p) for p in previous_keys]]:
            fernets.extend(self._fernets_for(k, primary=is_primary))
        self._fernet = MultiFernet(fernets)

    def _fernets_for(self, key: str, *, primary: bool) -> list[Fernet]:
        """Fernet instance(s) a single key contributes (most-preferred first).

        A raw Fernet key contributes itself. A passphrase contributes its
        scrypt-derived key (when a salt is available) followed by its legacy
        ``sha256`` key as a decrypt-only fallback. For the **primary** the first
        returned instance is the one ``encrypt`` uses, so the scrypt key (modern)
        leads when present, else the legacy key.
        """
        from cryptography.fernet import Fernet  # lazy, optional dependency

        raw = key.encode() if isinstance(key, str) else key
        try:
            return [Fernet(raw)]  # already a valid Fernet key — fast path, no KDF
        except Exception:
            pass
        out: list[Fernet] = []
        if self._salt is not None:
            out.append(Fernet(self._scrypt_key(raw, self._salt)))
        legacy = Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))
        out.append(legacy)
        return out

    @staticmethod
    def _scrypt_key(raw: bytes, salt: bytes) -> bytes:
        """Derive a urlsafe-base64 Fernet key from ``raw`` via salted scrypt."""
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt  # lazy

        kdf = Scrypt(salt=salt, length=_SCRYPT_DKLEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
        return base64.urlsafe_b64encode(kdf.derive(raw))

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)

    def rotate(self, token: bytes) -> bytes:
        # MultiFernet.rotate decrypts with any held key, re-encrypts to the primary.
        return self._fernet.rotate(token)


def _resolve_salt(config: HydroConfig, conn: sqlite3.Connection | None) -> bytes:
    """Load (or first-time generate + persist) this vault's passphrase salt.

    The salt lives in the vault ``vault_meta`` table so the SAME vault re-derives
    the SAME key on reopen (scrypt is deterministic given salt+passphrase) while
    two different vault DBs get independent salts. When no ``conn`` is supplied we
    open a short-lived connection on ``config.db_path`` (the vault store has
    already created the file), persist there, and close.
    """
    if conn is not None:
        return MetaStore(conn).get_or_create_salt(_SALT_META_KEY, lambda: os.urandom(_SALT_BYTES))
    own = sqlite3.connect(config.db_path)
    own.row_factory = sqlite3.Row
    try:
        salt = MetaStore(own).get_or_create_salt(_SALT_META_KEY, lambda: os.urandom(_SALT_BYTES))
        own.commit()
        return salt
    finally:
        own.close()


def _is_raw_fernet_key(key: str) -> bool:
    """True if ``key`` is already a valid urlsafe-base64 Fernet key (fast path)."""
    from cryptography.fernet import Fernet  # lazy, optional dependency

    try:
        Fernet(key.encode() if isinstance(key, str) else key)
        return True
    except Exception:
        return False


def build_cipher(config: HydroConfig, *, conn: sqlite3.Connection | None = None) -> Cipher:
    """Select the cipher from config: Fernet when keyed, else NullCipher (dev).

    Retired keys (``config.vault_prev_keys``) are passed through so a rotation can
    still decrypt rows written under the previous key. A raw Fernet key uses the
    unchanged fast path (no KDF, no DB access); a **passphrase** is stretched with
    a per-vault scrypt salt persisted in ``vault_meta`` (loaded via ``conn`` when
    given, else a short-lived connection on ``config.db_path``).
    """
    key = getattr(config, "vault_key", None)
    if key:
        prev = list(getattr(config, "vault_prev_keys", None) or [])
        # Only a passphrase needs a salt; a raw Fernet key (and raw retired keys)
        # take the fast path. Resolve a salt if ANY supplied key is a passphrase.
        needs_salt = any(not _is_raw_fernet_key(k) for k in (key, *prev))
        salt = _resolve_salt(config, conn) if needs_salt else None
        return FernetCipher(key, previous_keys=prev, salt=salt)
    logger.warning(
        "HydroMemory vault: no HYDRO_VAULT_KEY configured; using NullCipher "
        "(content stored as PLAINTEXT). Set a key for real encryption."
    )
    return NullCipher()
