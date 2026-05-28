"""L4 Sovereign Cognitive OS — capability/consent grant protocol (contract).

An app/platform REQUESTS scoped access to the user's memory; the owner approves;
an approved :class:`Grant` is enforced on every access by :func:`enforce_grant`,
which wraps governance ``check_access`` and can only ever NARROW it (allow→deny),
never widen. Phase A0 freezes the types + signatures; Phase B1 implements them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from hydromemory.governance import AccessDecision
from hydromemory.reservoirs import Reservoir, normalize_reservoir
from hydromemory.schema import new_id

if TYPE_CHECKING:
    import sqlite3

    from hydromemory.governance import AccessContext, AgentIdentity, Operation
    from hydromemory.schema import Droplet
    from hydromemory.vault.audit import AuditLog

# Additive, idempotent DDL — the grant store's own table.
GRANTS_DDL = """
CREATE TABLE IF NOT EXISTS grants (
    request_id      TEXT PRIMARY KEY,
    app_id          TEXT,
    owner           TEXT,
    reservoirs_json TEXT,
    operations_json TEXT,
    purpose         TEXT,
    status          TEXT,
    granted_at      TEXT,
    expiry          TEXT
)
"""


class GrantStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass
class GrantRequest:
    app_id: str
    owner: str
    reservoirs: list[Reservoir]
    operations: list[Any]  # list[Operation]
    purpose: str
    expiry: datetime | None = None
    request_id: str = field(default_factory=new_id)


@dataclass
class Grant:
    request_id: str
    app_id: str
    owner: str
    reservoirs: frozenset[Reservoir]
    operations: frozenset[Any]  # frozenset[Operation]
    purpose: str
    status: GrantStatus
    granted_at: datetime | None = None
    expiry: datetime | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _dump_reservoirs(reservoirs: Any) -> str:
    return json.dumps([normalize_reservoir(r).value for r in reservoirs])


def _load_reservoirs(text: str | None) -> frozenset[Reservoir]:
    return frozenset(normalize_reservoir(v) for v in json.loads(text or "[]"))


def _dump_operations(operations: Any) -> str:
    return json.dumps([getattr(op, "value", op) for op in operations])


def _load_operations(text: str | None) -> frozenset[Any]:
    # Import locally so the module stays importable even if governance is
    # being re-arranged; Operation is a (str, Enum) keyed by its ``.value``.
    from hydromemory.governance import Operation

    return frozenset(Operation(v) for v in json.loads(text or "[]"))


class GrantStore:
    """Persists grant requests/decisions in the ``grants`` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.execute(GRANTS_DDL)
        self._conn.commit()

    # -- internal helpers -----------------------------------------------------

    def _row_to_grant(self, row: Any) -> Grant:
        (
            request_id,
            app_id,
            owner,
            reservoirs_json,
            operations_json,
            purpose,
            status,
            granted_at,
            expiry,
        ) = row
        return Grant(
            request_id=request_id,
            app_id=app_id,
            owner=owner,
            reservoirs=_load_reservoirs(reservoirs_json),
            operations=_load_operations(operations_json),
            purpose=purpose,
            status=GrantStatus(status),
            granted_at=_parse_dt(granted_at),
            expiry=_parse_dt(expiry),
        )

    def _get(self, request_id: str) -> Grant | None:
        cur = self._conn.execute(
            "SELECT request_id, app_id, owner, reservoirs_json, operations_json, "
            "purpose, status, granted_at, expiry FROM grants WHERE request_id = ?",
            (request_id,),
        )
        row = cur.fetchone()
        return self._row_to_grant(row) if row is not None else None

    def _set_status(
        self,
        request_id: str,
        owner: str,
        status: GrantStatus,
        *,
        set_granted_at: bool = False,
    ) -> Grant:
        existing = self._get(request_id)
        if existing is None:
            raise KeyError(f"no grant request '{request_id}'")
        if existing.owner != owner:
            raise PermissionError(
                f"grant '{request_id}' is owned by '{existing.owner}', not '{owner}'"
            )
        granted_at = _fmt_dt(_now()) if set_granted_at else _fmt_dt(existing.granted_at)
        self._conn.execute(
            "UPDATE grants SET status = ?, granted_at = ? WHERE request_id = ?",
            (status.value, granted_at, request_id),
        )
        self._conn.commit()
        updated = self._get(request_id)
        assert updated is not None
        return updated

    # -- public API -----------------------------------------------------------

    def request(self, req: GrantRequest) -> Grant:
        """Persist a new PENDING grant for an app's access request."""
        self._conn.execute(
            "INSERT INTO grants (request_id, app_id, owner, reservoirs_json, "
            "operations_json, purpose, status, granted_at, expiry) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                req.request_id,
                req.app_id,
                req.owner,
                _dump_reservoirs(req.reservoirs),
                _dump_operations(req.operations),
                req.purpose,
                GrantStatus.PENDING.value,
                None,
                _fmt_dt(req.expiry),
            ),
        )
        self._conn.commit()
        grant = self._get(req.request_id)
        assert grant is not None
        return grant

    def approve(self, request_id: str, owner: str) -> Grant:
        """Owner-only: transition a request to APPROVED and stamp ``granted_at``."""
        return self._set_status(
            request_id, owner, GrantStatus.APPROVED, set_granted_at=True
        )

    def deny(self, request_id: str, owner: str) -> Grant:
        """Owner-only: transition a request to DENIED."""
        return self._set_status(request_id, owner, GrantStatus.DENIED)

    def revoke(self, request_id: str, owner: str) -> Grant:
        """Owner-only: transition a (typically approved) grant to REVOKED."""
        return self._set_status(request_id, owner, GrantStatus.REVOKED)

    def active_for(self, app_id: str) -> list[Grant]:
        """Return APPROVED, non-expired, non-revoked grants for ``app_id``.

        A grant whose ``expiry`` is in the past is treated as EXPIRED and is
        excluded (the stored status is left untouched — expiry is evaluated
        lazily at read time against the wall clock).
        """
        cur = self._conn.execute(
            "SELECT request_id, app_id, owner, reservoirs_json, operations_json, "
            "purpose, status, granted_at, expiry FROM grants "
            "WHERE app_id = ? AND status = ?",
            (app_id, GrantStatus.APPROVED.value),
        )
        now = _now()
        out: list[Grant] = []
        for row in cur.fetchall():
            grant = self._row_to_grant(row)
            if grant.expiry is not None and grant.expiry <= now:
                continue  # past expiry -> treat as EXPIRED, not active
            out.append(grant)
        return out

    def list(self, owner: str) -> list[Grant]:
        """Return every grant belonging to ``owner`` (any status)."""
        cur = self._conn.execute(
            "SELECT request_id, app_id, owner, reservoirs_json, operations_json, "
            "purpose, status, granted_at, expiry FROM grants WHERE owner = ?",
            (owner,),
        )
        return [self._row_to_grant(row) for row in cur.fetchall()]


def enforce_grant(
    droplet: Droplet,
    agent: AgentIdentity,
    context: AccessContext,
    operation: Operation,
    *,
    app_id: str | None,
    store: GrantStore,
    audit: AuditLog | None = None,
) -> AccessDecision:
    """Governance ``check_access`` AND an active capability grant (narrow-only).

    Composition is reservoir policy ∧ droplet permissions ∧ grant — a pure AND,
    so a grant can only ever NARROW the base governance decision, never widen
    it. The steps:

    1. Call ``check_access`` first. If it denies, return that decision unchanged
       (a grant cannot resurrect a governance denial).
    2. If ``agent.is_user_proxy`` (the owner acting directly — L2), return the
       base decision: the owner bypasses the app-grant layer entirely.
    3. Otherwise require an active grant for ``(app_id, droplet owner)`` whose
       ``reservoirs`` contains the droplet's reservoir and whose ``operations``
       contains ``operation``. If none matches, return a DENY decision.
    4. On allow, append an audit entry if an ``audit`` log was provided.
    """
    from hydromemory.governance import check_access

    base = check_access(droplet, agent, context, operation)
    if not base.allowed:
        # Grants never widen: a denied base decision is returned as-is.
        return base

    # L2: the owner (user proxy) bypasses the app-grant layer.
    if agent.is_user_proxy:
        return base

    owner = droplet.permissions.owner

    def deny(reason: str) -> AccessDecision:
        decision = AccessDecision(
            allowed=False,
            denial_reason=reason,
            obligations=list(base.obligations),
            usable_for_generation=base.usable_for_generation,
        )
        _audit(audit, agent, app_id, operation, droplet, decision)
        return decision

    if app_id is None:
        return deny("no app_id supplied; an app grant is required for non-owner access")

    grants = store.active_for(app_id)
    matched = next(
        (
            g
            for g in grants
            if g.owner == owner
            and droplet.reservoir in g.reservoirs
            and operation in g.operations
        ),
        None,
    )
    if matched is None:
        return deny(
            f"no active grant for app '{app_id}' covering "
            f"reservoir '{droplet.reservoir.value}' + operation '{operation.value}'"
        )

    _audit(audit, agent, app_id, operation, droplet, base)
    return base


def _audit(
    audit: AuditLog | None,
    agent: AgentIdentity,
    app_id: str | None,
    operation: Operation,
    droplet: Droplet,
    decision: AccessDecision,
) -> None:
    if audit is None:
        return
    audit.append(
        actor=agent.name,
        app_id=app_id,
        operation=operation.value,
        droplet_id=droplet.id,
        decision=decision,
        detail="enforce_grant",
    )
