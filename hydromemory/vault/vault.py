"""VaultRepository — the encrypted, audited, access-enforced, app-scoped store.

Implements :class:`~hydromemory.storage.repository.DropletRepository` by wrapping
a backing repository (the plain ``SqliteDropletRepository``) and adding, on every
method: per-app scope filtering (L1), ``check_access`` enforcement, an audit-log
entry, and encryption-at-rest of content/state/tags/cycle/meta (routing columns
stay plaintext; the vector index holds decrypted-in-process vectors).

Encrypt-which-fields (the crux). The backing repo persists a Droplet by reading
``droplet.to_dict()`` and writing promoted columns plus ``*_json`` sidecars. To
keep ``query`` + ``check_access`` working we must leave the routing/governance
columns plaintext (phase, reservoir, memory_type, owner, visibility, retention,
external_sharing, purity, app_id). Everything secret — content, semantic_tags,
the full state vector, cycle, and meta — is serialized into ONE canonical JSON
payload, encrypted to a single token, and stashed in ``meta["__vault__"]`` of the
*on-disk* droplet handed to the backing repo. That on-disk droplet carries:

* ``content`` = the ciphertext token (so the ``content`` column is ciphertext);
* ``semantic_tags`` = ``[]`` and ``cycle`` = empty (their ``*_json`` leak nothing);
* ``state`` = a State with only ``purity`` preserved (the plaintext ``purity``
  column stays queryable; ``state_json`` leaks only purity + zeros);
* unchanged ``phase`` / ``reservoir`` / ``permissions`` (plaintext routing);
* the original ``embedding`` (the backing repo stores it in ``.vec.npz`` /
  ``meta["__embedding__"]`` as plaintext — a *documented* in-process leak so the
  vector index and ``rebuild_index`` keep working under encryption).

On read we pull ``meta["__vault__"]``, decrypt it, and restore the secret fields,
returning a fully-decrypted Droplet. ``app_id`` is written with a small direct
UPDATE on the backing connection (the backing upsert never sets that column).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from hydromemory.governance import (
    AccessContext,
    AccessDecision,
    AgentIdentity,
    Operation,
    check_access,
)
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Cycle, Droplet, Phase, State, Visibility
from hydromemory.storage.repository import DropletRepository
from hydromemory.vault.audit import AuditLog
from hydromemory.vault.cipher import Cipher
from hydromemory.vault.scope import AppScope

# Meta key under which the single encrypted payload is stored on the on-disk
# droplet. Stripped on read so a round-tripped ``droplet.meta`` never leaks it.
_VAULT_META_KEY = "__vault__"
# Mirror of the backing repo's reserved embedding meta key (see
# ``hydromemory.storage.sqlite_repository._EMBED_META_KEY``) — kept out of the
# encrypted payload because the backing repo owns it.
_EMBED_META_KEY = "__embedding__"


class VaultRepository(DropletRepository):
    def __init__(
        self,
        backing: DropletRepository,
        cipher: Cipher,
        audit: AuditLog,
        *,
        identity: AgentIdentity,
        scope: AppScope,
        context: AccessContext | None = None,
    ) -> None:
        self.backing = backing
        self.cipher = cipher
        self.audit = audit
        self.identity = identity
        self.scope = scope
        self.context = context or AccessContext()

    # ------------------------------------------------------------ encryption
    def _encrypt_for_disk(self, droplet: Droplet) -> Droplet:
        """Return an on-disk copy of ``droplet`` with secret fields encrypted.

        Content/semantic_tags/full-state/cycle/meta are packed into one canonical
        JSON payload, encrypted, and parked in ``meta[_VAULT_META_KEY]``. Routing
        columns + permissions + embedding are preserved as plaintext.
        """
        # Drop the backing repo's reserved embedding key from the secret payload;
        # the backing repo re-adds it (plaintext) from ``droplet.embedding``.
        clean_meta = {k: v for k, v in droplet.meta.items() if k != _EMBED_META_KEY}
        payload = {
            "content": droplet.content,
            "semantic_tags": list(droplet.semantic_tags),
            "state": droplet.state.to_dict(),
            "cycle": droplet.cycle.to_dict(),
            "meta": clean_meta,
        }
        token = self.cipher.encrypt(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).decode("ascii")

        # Keep only ``purity`` in the plaintext state (queryable column); the real
        # vector lives encrypted in the payload above.
        disk_state = State(purity=droplet.state.purity)
        on_disk = Droplet(
            id=droplet.id,
            content=token,
            source=droplet.source,
            created_at=droplet.created_at,
            phase=droplet.phase,
            reservoir=droplet.reservoir,
            memory_type=droplet.memory_type,
            semantic_tags=[],
            state=disk_state,
            permissions=droplet.permissions,
            links=droplet.links,
            cycle=Cycle(),
            meta={_VAULT_META_KEY: token},
            embedding=droplet.embedding,
        )
        return on_disk

    def _decrypt_from_disk(self, on_disk: Droplet) -> Droplet:
        """Reverse :meth:`_encrypt_for_disk`, restoring the secret fields.

        Tolerates an un-encrypted row (no ``_VAULT_META_KEY``) by returning it
        unchanged, so a plain store read under a NullCipher path still works.
        """
        token = on_disk.meta.get(_VAULT_META_KEY)
        if token is None:
            return on_disk
        raw = self.cipher.decrypt(token.encode("ascii"))
        payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
        on_disk.content = str(payload.get("content", ""))
        on_disk.semantic_tags = list(payload.get("semantic_tags", []))
        on_disk.state = State.from_dict(payload.get("state"))
        on_disk.cycle = Cycle.from_dict(payload.get("cycle"))
        on_disk.meta = dict(payload.get("meta", {}))
        return on_disk

    # --------------------------------------------------------------- scoping
    @property
    def _conn(self) -> sqlite3.Connection:
        """The backing SQLite connection (for the ``app_id`` column + scope SQL)."""
        conn = getattr(self.backing, "_conn", None)
        if conn is None:  # pragma: no cover - defensive; backing is always sqlite here
            raise RuntimeError("VaultRepository requires a SQLite-backed repository")
        return conn

    def _tag_app_id(self, droplet_id: str) -> None:
        """Stamp the ``app_id`` column for ``droplet_id`` with the scope's app."""
        if self.scope.cross_app:
            return
        self._conn.execute(
            "UPDATE droplets SET app_id = ? WHERE id = ?",
            (self.scope.app_id, droplet_id),
        )
        self._conn.commit()

    def _in_scope(self, droplet_id: str) -> bool:
        """Whether ``droplet_id`` belongs to this scope (cross-app sees all)."""
        if self.scope.cross_app:
            return True
        row = self._conn.execute(
            "SELECT app_id FROM droplets WHERE id = ?", (droplet_id,)
        ).fetchone()
        if row is None:
            return False
        return row["app_id"] == self.scope.app_id

    def _scoped_ids(self) -> set[str] | None:
        """The id set visible to this scope, or ``None`` for cross-app (all)."""
        if self.scope.cross_app:
            return None
        rows = self._conn.execute(
            "SELECT id FROM droplets WHERE app_id IS ?", (self.scope.app_id,)
        ).fetchall()
        return {r["id"] for r in rows}

    # --------------------------------------------------------------- audit
    def _audit(
        self,
        operation: Operation,
        droplet_id: str | None,
        decision: AccessDecision,
        *,
        detail: str | None = None,
    ) -> None:
        self.audit.append(
            actor=self.identity.name,
            app_id=self.scope.app_id,
            operation=operation.value,
            droplet_id=droplet_id,
            decision=decision,
            detail=detail,
        )

    @staticmethod
    def _allow() -> AccessDecision:
        return AccessDecision(allowed=True)

    def _audit_out_of_scope(self, operation: Operation, droplet_id: str) -> None:
        """Record an out-of-scope (cross-app) access attempt as a denied entry.

        The scope filter short-circuits ahead of ``check_access``, but the
        *attempt* is still audited — so cross-scope probes are visible in the
        owner's tamper-evident log instead of being a silent miss (ADR-0021).
        """
        self._audit(
            operation,
            droplet_id,
            AccessDecision(allowed=False, denial_reason="out of app scope"),
        )

    # ------------------------------------------------------------------- CRUD
    def upsert(self, droplet: Droplet) -> None:
        # A write into the owner's vault: governance gate on MUTATE.
        decision = check_access(droplet, self.identity, self.context, Operation.MUTATE)
        self._audit(Operation.MUTATE, droplet.id, decision)
        if not decision.allowed:
            return
        self.backing.upsert(self._encrypt_for_disk(droplet))
        self._tag_app_id(droplet.id)

    def get(self, droplet_id: str) -> Droplet | None:
        if not self._in_scope(droplet_id):
            self._audit_out_of_scope(Operation.READ, droplet_id)
            return None
        on_disk = self.backing.get(droplet_id)
        if on_disk is None:
            return None
        droplet = self._decrypt_from_disk(on_disk)
        decision = check_access(droplet, self.identity, self.context, Operation.READ)
        self._audit(Operation.READ, droplet_id, decision)
        if not decision.allowed:
            return None
        return droplet

    def delete(self, droplet_id: str) -> None:
        if not self._in_scope(droplet_id):
            self._audit_out_of_scope(Operation.OVERWRITE, droplet_id)
            return
        on_disk = self.backing.get(droplet_id)
        if on_disk is None:
            self._audit(Operation.OVERWRITE, droplet_id, self._allow())
            self.backing.delete(droplet_id)
            return
        droplet = self._decrypt_from_disk(on_disk)
        decision = check_access(droplet, self.identity, self.context, Operation.OVERWRITE)
        self._audit(Operation.OVERWRITE, droplet_id, decision)
        if not decision.allowed:
            return
        self.backing.delete(droplet_id)

    def all_ids(self) -> list[str]:
        ids = self.backing.all_ids()
        scoped = self._scoped_ids()
        if scoped is None:
            return ids
        return [i for i in ids if i in scoped]

    # ------------------------------------------------------------------ query
    def query(
        self,
        *,
        reservoir: Reservoir | None = None,
        phase: Phase | None = None,
        memory_type: str | None = None,
        min_purity: float | None = None,
        visibility: Visibility | None = None,
        allowed_agent: str | None = None,
        usable_for_response_only: bool = False,
        limit: int | None = None,
    ) -> list[Droplet]:
        # Routing columns are plaintext, so the backing query still works; we add
        # the app-scope filter ourselves (the backing repo has no app concept).
        rows = self.backing.query(
            reservoir=reservoir,
            phase=phase,
            memory_type=memory_type,
            min_purity=min_purity,
            visibility=visibility,
            allowed_agent=allowed_agent,
            usable_for_response_only=usable_for_response_only,
            limit=None,
        )
        scoped = self._scoped_ids()
        results: list[Droplet] = []
        for on_disk in rows:
            if scoped is not None and on_disk.id not in scoped:
                continue
            droplet = self._decrypt_from_disk(on_disk)
            decision = check_access(droplet, self.identity, self.context, Operation.READ)
            self._audit(Operation.READ, droplet.id, decision)
            if not decision.allowed:
                continue
            results.append(droplet)
            if limit is not None and len(results) >= limit:
                break
        return results

    # ----------------------------------------------------------- similarity
    def search_similar(
        self,
        embedding: Sequence[float],
        k: int = 10,
        candidate_filter: Callable[[Droplet], bool] | None = None,
    ) -> list[tuple[str, float]]:
        """Vector search over the (plaintext, in-process) index.

        The index holds decrypted-in-process vectors, so cosine ranking is exact
        under encryption. We wrap ``candidate_filter`` so it sees a *decrypted*
        droplet, and always enforce app-scope + READ access on each hit.
        """
        scoped = self._scoped_ids()

        def _vault_filter(on_disk: Droplet) -> bool:
            if scoped is not None and on_disk.id not in scoped:
                return False
            droplet = self._decrypt_from_disk(on_disk)
            decision = check_access(droplet, self.identity, self.context, Operation.READ)
            if not decision.allowed:
                return False
            if candidate_filter is not None and not candidate_filter(droplet):
                return False
            return True

        return self.backing.search_similar(embedding, k, candidate_filter=_vault_filter)

    # ---------------------------------------------------------------- links
    def add_link(self, src_id: str, kind: str, dst_id: str) -> None:
        if not self._in_scope(src_id):
            self._audit_out_of_scope(Operation.MUTATE, src_id)
            return
        self.backing.add_link(src_id, kind, dst_id)

    def remove_link(self, src_id: str, kind: str, dst_id: str) -> None:
        if not self._in_scope(src_id):
            self._audit_out_of_scope(Operation.MUTATE, src_id)
            return
        self.backing.remove_link(src_id, kind, dst_id)

    # ---------------------------------------------------------------- cycle
    def touch_cycle(
        self,
        droplet_id: str,
        *,
        recalled: datetime | None = None,
        transformed: datetime | None = None,
        verified: datetime | None = None,
        increment_count: bool = False,
    ) -> None:
        """Update cycle metadata.

        Cycle is encrypted at rest, so we cannot delegate to the backing repo's
        plaintext ``cycle_json`` update: read-decrypt, mutate in memory, then
        re-encrypt via ``upsert`` (preserving the embedding and routing columns).
        """
        if not self._in_scope(droplet_id):
            self._audit_out_of_scope(Operation.MUTATE, droplet_id)
            return
        on_disk = self.backing.get(droplet_id)
        if on_disk is None:
            return
        droplet = self._decrypt_from_disk(on_disk)
        cycle = droplet.cycle
        if recalled is not None:
            cycle.last_recalled = recalled
        if transformed is not None:
            cycle.last_transformed = transformed
        if verified is not None:
            cycle.last_verified = verified
        if increment_count:
            cycle.cycle_count += 1
        self.backing.upsert(self._encrypt_for_disk(droplet))
        self._tag_app_id(droplet_id)

    # --------------------------------------------------------------- index
    def rebuild_index(self) -> None:
        # Embeddings are stored plaintext (the documented .vec.npz leak), so the
        # backing rebuild reloads them from rows directly — no decrypt needed.
        self.backing.rebuild_index()

    # ------------------------------------------------------------- rotation
    def rotate_keys(self) -> int:
        """Re-encrypt every stored droplet to the cipher's current primary key.

        Vault-wide and owner-only: re-keying is a property of the user's vault,
        not of an app scope, so this **ignores the L1/L2 scope and walks all
        rows**, but requires a user-proxy (owner) identity. Each row's single
        ciphertext token is rotated via ``cipher.rotate`` (decrypt with any held
        key, re-encrypt to the primary) and written back; routing columns,
        ``app_id``, embeddings, and links are untouched. Idempotent and safe to
        re-run — already-rotated rows re-rotate to the same primary, and a partial
        run leaves a mix the multi-key cipher still decrypts. Returns the count of
        rows re-encrypted. Raises if a token cannot be decrypted with any
        configured key (a missing retired key — fail loud rather than lose data).
        """
        if not self.identity.is_user_proxy:
            denial = AccessDecision(allowed=False, denial_reason="key rotation requires the vault owner")
            self.audit.append(
                actor=self.identity.name,
                app_id=None,
                operation="rotate_keys",
                droplet_id=None,
                decision=denial,
            )
            raise PermissionError("vault key rotation requires a user-proxy (owner) identity")

        rotated = 0
        for droplet_id in self.backing.all_ids():
            on_disk = self.backing.get(droplet_id)
            if on_disk is None:
                continue
            token = on_disk.meta.get(_VAULT_META_KEY)
            if token is None:
                continue  # row without an encrypted payload — nothing to rotate
            try:
                new_token = self.cipher.rotate(token.encode("ascii")).decode("ascii")
            except Exception as exc:  # noqa: BLE001 -- name the row, then fail loudly
                raise RuntimeError(
                    f"key rotation failed for droplet {droplet_id!r}: its token is not decryptable "
                    "with any configured key. Add the prior key to HYDRO_VAULT_PREV_KEYS and retry."
                ) from exc
            if new_token == token:
                continue  # NullCipher / no-op rotation — leave the row untouched
            on_disk.content = new_token
            on_disk.meta[_VAULT_META_KEY] = new_token
            self.backing.upsert(on_disk)
            rotated += 1

        self.audit.append(
            actor=self.identity.name,
            app_id=None,
            operation="rotate_keys",
            droplet_id=None,
            decision=self._allow(),
            detail=f"re-encrypted {rotated} droplet(s) to the primary key",
        )
        return rotated

    @staticmethod
    def _is_plaintext_payload(token: str) -> bool:
        """True if ``token`` is an un-encrypted payload (a JSON object), not ciphertext.

        A NullCipher row stores the canonical payload verbatim, so its token parses
        as a JSON object; a Fernet token is base64 and never parses as JSON.
        """
        try:
            parsed = json.loads(token)
        except (json.JSONDecodeError, ValueError):
            return False
        return isinstance(parsed, dict)

    def encrypt_plaintext_rows(self) -> int:
        """Encrypt rows written while keyless (NullCipher/plaintext) under the key.

        The one-time **keyless -> encrypted** migration that ``rotate_keys``
        deliberately does not do (it rotates *between* Fernet keys). Owner-only and
        vault-wide; requires a configured key (a Fernet cipher). Each row whose
        token is still a plaintext payload is Fernet-encrypted in place; rows that
        are already ciphertext (or non-payload) are left untouched, so a mixed
        vault migrates cleanly and a re-run is a no-op. Returns the count encrypted.
        """
        if not self.identity.is_user_proxy:
            denial = AccessDecision(allowed=False, denial_reason="vault encryption requires the owner")
            self.audit.append(
                actor=self.identity.name,
                app_id=None,
                operation="encrypt_plaintext_rows",
                droplet_id=None,
                decision=denial,
            )
            raise PermissionError("encrypting the vault requires a user-proxy (owner) identity")
        if getattr(self.cipher, "label", "") != "fernet":
            raise RuntimeError(
                "no encryption key configured; set HYDRO_VAULT_KEY before encrypting the vault "
                "(a keyless NullCipher vault has nothing to encrypt to)."
            )

        encrypted = 0
        for droplet_id in self.backing.all_ids():
            on_disk = self.backing.get(droplet_id)
            if on_disk is None:
                continue
            token = on_disk.meta.get(_VAULT_META_KEY)
            if token is None or not self._is_plaintext_payload(token):
                continue  # already ciphertext (or no payload) — leave it
            new_token = self.cipher.encrypt(token.encode("utf-8")).decode("ascii")
            on_disk.content = new_token
            on_disk.meta[_VAULT_META_KEY] = new_token
            self.backing.upsert(on_disk)
            encrypted += 1

        self.audit.append(
            actor=self.identity.name,
            app_id=None,
            operation="encrypt_plaintext_rows",
            droplet_id=None,
            decision=self._allow(),
            detail=f"encrypted {encrypted} previously-plaintext droplet(s)",
        )
        return encrypted

    def close(self) -> None:
        self.backing.close()
