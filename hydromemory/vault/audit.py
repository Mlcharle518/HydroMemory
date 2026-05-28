"""Append-only, hash-chained audit log for the vault (PRD §9).

Every read/write/query and every access decision is recorded as an
:class:`AuditEntry` in the ``audit`` table. Entries are chained
(``entry_hash = sha256(prev_hash || canonical(entry))``), which makes *edits,
insertion, and reordering* detectable. The chain alone cannot, however, detect
**tail truncation** — lopping off the most recent rows leaves a shorter but
internally-consistent chain. To close that gap a **head watermark** (the max seq
and last ``entry_hash``) is persisted in the ``vault_meta`` table on every append;
:meth:`AuditLog.verify_chain` fails if the actual last row is behind that
watermark. So the log is tamper-evident *given an intact watermark* — see
:meth:`AuditLog.verify_chain`.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hydromemory.governance import AccessDecision

# Additive, idempotent DDL — created by the vault on first use (own table).
AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT,
    actor            TEXT,
    app_id           TEXT,
    operation        TEXT,
    droplet_id       TEXT,
    allowed          INTEGER,
    obligations_json TEXT,
    detail           TEXT,
    prev_hash        TEXT,
    entry_hash       TEXT
)
"""

# Additive, idempotent DDL for a tiny key/value sidecar table shared by the vault
# (passphrase KDF salt) and the audit log (head watermark). Created on first use.
VAULT_META_DDL = """
CREATE TABLE IF NOT EXISTS vault_meta (
    name  TEXT PRIMARY KEY,
    value BLOB
)
"""

# vault_meta keys for the audit head watermark (defeats tail truncation).
_AUDIT_HEAD_SEQ_KEY = "audit_head_seq"
_AUDIT_HEAD_HASH_KEY = "audit_head_hash"

# The canonical "no previous entry" hash that seeds the chain.
_GENESIS_HASH = "0" * 64


class MetaStore:
    """Tiny key/value accessor over the idempotent ``vault_meta`` table.

    Shared infrastructure: the cipher persists its per-vault scrypt salt here and
    the audit log persists its head watermark here. Values are stored as BLOBs;
    string helpers encode/decode UTF-8.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute(VAULT_META_DDL)
        self._conn.commit()

    def get(self, name: str) -> bytes | None:
        row = self._conn.execute(
            "SELECT value FROM vault_meta WHERE name = ?", (name,)
        ).fetchone()
        return None if row is None else bytes(row["value"])

    def set(self, name: str, value: bytes) -> None:
        self._conn.execute(
            "INSERT INTO vault_meta (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value),
        )
        self._conn.commit()

    def get_str(self, name: str) -> str | None:
        raw = self.get(name)
        return None if raw is None else raw.decode("utf-8")

    def set_str(self, name: str, value: str) -> None:
        self.set(name, value.encode("utf-8"))

    def get_or_create_salt(self, name: str, factory: Callable[[], bytes]) -> bytes:
        """Return the persisted salt under ``name``, generating + storing it once."""
        existing = self.get(name)
        if existing is not None:
            return existing
        salt = factory()
        self.set(name, salt)
        return salt


@dataclass
class AuditEntry:
    seq: int
    timestamp: datetime
    actor: str
    app_id: str | None
    operation: str
    droplet_id: str | None
    allowed: bool
    obligations: list[str]
    detail: str | None
    prev_hash: str
    entry_hash: str


def _canonical(payload: dict[str, Any]) -> str:
    """Deterministic JSON for hashing (sorted keys, no whitespace, UTF-8 safe)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_fields(
    *,
    prev_hash: str,
    ts: str,
    actor: str,
    app_id: str | None,
    operation: str,
    droplet_id: str | None,
    allowed: bool,
    obligations: list[str],
    detail: str | None,
) -> str:
    """Compute ``sha256(prev_hash || canonical(fields))`` as a hex digest."""
    body = _canonical(
        {
            "ts": ts,
            "actor": actor,
            "app_id": app_id,
            "operation": operation,
            "droplet_id": droplet_id,
            "allowed": bool(allowed),
            "obligations": list(obligations),
            "detail": detail,
        }
    )
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only audit trail over the ``audit`` table.

    Tamper-evident *given an intact head watermark*: the hash chain detects edits,
    insertion, and reordering on its own; tail truncation is caught by the seq/hash
    watermark persisted in ``vault_meta`` on every append (see
    :meth:`verify_chain`).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute(AUDIT_DDL)
        self._conn.commit()
        self._meta = MetaStore(conn)

    def append(
        self,
        *,
        actor: str,
        app_id: str | None,
        operation: str,
        droplet_id: str | None,
        decision: AccessDecision,
        detail: str | None = None,
    ) -> AuditEntry:
        """Record one access decision, extending the hash chain, and return it."""
        ts = datetime.now(UTC).isoformat()
        obligations = [getattr(o, "value", str(o)) for o in decision.obligations]
        allowed = bool(decision.allowed)
        if detail is None and decision.denial_reason is not None:
            detail = decision.denial_reason

        prev_row = self._conn.execute(
            "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev_row["entry_hash"] if prev_row is not None else _GENESIS_HASH

        entry_hash = _hash_fields(
            prev_hash=prev_hash,
            ts=ts,
            actor=actor,
            app_id=app_id,
            operation=operation,
            droplet_id=droplet_id,
            allowed=allowed,
            obligations=obligations,
            detail=detail,
        )
        cur = self._conn.execute(
            """
            INSERT INTO audit (
                ts, actor, app_id, operation, droplet_id,
                allowed, obligations_json, detail, prev_hash, entry_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                actor,
                app_id,
                operation,
                droplet_id,
                1 if allowed else 0,
                _canonical({"obligations": obligations}),
                detail,
                prev_hash,
                entry_hash,
            ),
        )
        self._conn.commit()
        seq = int(cur.lastrowid or 0)
        # Advance the head watermark (max seq + last hash) so a later tail
        # truncation — which leaves a shorter but self-consistent chain — is
        # detected by verify_chain. seq is monotonic (AUTOINCREMENT), so this row
        # is the new head.
        self._meta.set_str(_AUDIT_HEAD_SEQ_KEY, str(seq))
        self._meta.set_str(_AUDIT_HEAD_HASH_KEY, entry_hash)
        return AuditEntry(
            seq=seq,
            timestamp=datetime.fromisoformat(ts),
            actor=actor,
            app_id=app_id,
            operation=operation,
            droplet_id=droplet_id,
            allowed=allowed,
            obligations=obligations,
            detail=detail,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )

    def query(self, **filters: Any) -> list[AuditEntry]:
        """Filter audit entries by actor/app_id/droplet_id/operation/allowed/since.

        Recognised keyword filters: ``actor``, ``app_id``, ``droplet_id``,
        ``operation`` (equality); ``allowed`` (bool); ``since`` (datetime|ISO str,
        inclusive lower bound on ``ts``); ``limit`` (max rows, most recent kept).
        """
        clauses: list[str] = []
        params: list[Any] = []
        for col in ("actor", "app_id", "droplet_id", "operation"):
            if col in filters and filters[col] is not None:
                clauses.append(f"{col} = ?")
                params.append(filters[col])
        if filters.get("allowed") is not None:
            clauses.append("allowed = ?")
            params.append(1 if filters["allowed"] else 0)
        since = filters.get("since")
        if since is not None:
            since_ts = since.isoformat() if isinstance(since, datetime) else str(since)
            clauses.append("ts >= ?")
            params.append(since_ts)

        sql = "SELECT * FROM audit"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq"

        rows = self._conn.execute(sql, params).fetchall()
        entries = [self._row_to_entry(r) for r in rows]
        limit = filters.get("limit")
        if limit is not None:
            entries = entries[-int(limit):] if limit else []
        return entries

    def verify_chain(self) -> bool:
        """Recompute the chain from row 0; return False on any inconsistency.

        Catches edits, insertion, and reordering by recomputing every link, and
        catches **tail truncation** by comparing the actual last row against the
        persisted head watermark (max seq + last ``entry_hash``): if the watermark
        is ahead of the real last row (rows were lopped off the end), the chain is
        rejected. A log with no watermark (e.g. one written before watermarking, or
        a genuinely empty log) is not failed on that basis alone, preserving
        backward compatibility.
        """
        rows = self._conn.execute(
            "SELECT * FROM audit ORDER BY seq"
        ).fetchall()
        prev_hash = _GENESIS_HASH
        last_seq = 0
        for row in rows:
            if row["prev_hash"] != prev_hash:
                return False
            obligations = list(json.loads(row["obligations_json"] or "{}").get("obligations", []))
            expected = _hash_fields(
                prev_hash=prev_hash,
                ts=row["ts"],
                actor=row["actor"],
                app_id=row["app_id"],
                operation=row["operation"],
                droplet_id=row["droplet_id"],
                allowed=bool(row["allowed"]),
                obligations=obligations,
                detail=row["detail"],
            )
            if expected != row["entry_hash"]:
                return False
            prev_hash = row["entry_hash"]
            last_seq = int(row["seq"])

        return self._watermark_ok(last_seq=last_seq, last_hash=prev_hash)

    def _watermark_ok(self, *, last_seq: int, last_hash: str) -> bool:
        """Whether the actual chain head matches the persisted watermark.

        Returns ``True`` when no watermark is recorded (nothing to enforce). When a
        watermark exists, the real last row must be at least as far along: a
        recorded head seq greater than ``last_seq`` (or a head hash that doesn't
        match the last row's) means the tail was truncated.
        """
        head_seq_raw = self._meta.get_str(_AUDIT_HEAD_SEQ_KEY)
        head_hash = self._meta.get_str(_AUDIT_HEAD_HASH_KEY)
        if head_seq_raw is None or head_hash is None:
            return True  # no watermark recorded — nothing to enforce
        try:
            head_seq = int(head_seq_raw)
        except ValueError:  # pragma: no cover - defensive; watermark is always int
            return False
        if last_seq < head_seq:
            return False  # rows were deleted off the tail
        if last_hash != head_hash:
            return False  # head hash diverged (tail replaced/truncated)
        return True

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
        obligations = list(json.loads(row["obligations_json"] or "{}").get("obligations", []))
        return AuditEntry(
            seq=int(row["seq"]),
            timestamp=datetime.fromisoformat(row["ts"]),
            actor=row["actor"],
            app_id=row["app_id"],
            operation=row["operation"],
            droplet_id=row["droplet_id"],
            allowed=bool(row["allowed"]),
            obligations=obligations,
            detail=row["detail"],
            prev_hash=row["prev_hash"],
            entry_hash=row["entry_hash"],
        )
