"""L1 App Memory handle (contract; impl in Phase B1).

An :class:`AppMemory` binds an ``app_id`` to a scoped vault view + a bus client +
its grant store, so an application absorbs/recalls only within its own scope and
requests broader access via the L4 grant protocol.

``recall`` is the enforcement seam: every candidate the vault surfaces is routed
through :func:`enforce_grant` for the requesting agent, so an app only ever sees
droplets its (owner-approved) grant + governance both permit. A user-proxy agent
(the owner) bypasses the grant layer (L2). ``absorb`` tags the new droplet with
its ``app_id`` and announces it on the bus (ABSORBED), which is what wakes the L3
mesh. ``request_access`` files an L4 :class:`GrantRequest`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from hydromemory.bus.events import EventType, MemoryEvent
from hydromemory.governance import AccessContext, AgentIdentity, Operation
from hydromemory.platform.grants import GrantRequest, enforce_grant
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, new_id


@dataclass
class AppMemory:
    app_id: str
    owner: str
    vault: Any  # VaultRepository scoped to app_id (L1)
    bus: Any  # EventBus
    store: Any  # GrantStore

    def absorb(self, content: str, **kwargs: Any) -> dict[str, Any]:
        """Persist a droplet tagged with this app's ``app_id`` and announce it.

        The droplet's ``meta["app_id"]`` is stamped so the L1 scope filter can
        attribute it; an ABSORBED :class:`MemoryEvent` is published so the L3
        mesh (and any other subscribers) can react. Extra ``kwargs`` are passed
        straight to :class:`~hydromemory.schema.Droplet` (e.g. ``reservoir``,
        ``phase``, ``source``).
        """
        meta = dict(kwargs.pop("meta", {}) or {})
        meta["app_id"] = self.app_id
        droplet = Droplet(
            id=kwargs.pop("id", None) or new_id(),
            content=content,
            meta=meta,
            **kwargs,
        )
        # Default ownership to the app's owner unless the caller overrode it.
        if "permissions" not in kwargs:
            droplet.permissions.owner = self.owner
        self.vault.upsert(droplet)
        self.bus.publish(
            MemoryEvent(
                type=EventType.ABSORBED.value,
                actor=self.app_id,
                droplet_id=droplet.id,
                app_id=self.app_id,
                payload={"_depth": 0},
            )
        )
        return droplet.to_dict()

    def recall(self, query: str, agent: Any) -> list[Any]:
        """Return candidate droplets the ``agent`` may READ under this app's grant.

        Every candidate the vault surfaces is run through :func:`enforce_grant`
        with this app's ``app_id``; only allowed droplets are returned. ``agent``
        may be an :class:`AgentIdentity` or any object exposing ``identity()``.

        Security (H2): ``is_user_proxy`` bypasses the L4 grant layer entirely, so
        it is honored *only* when the caller's identity name matches this app's
        ``owner`` (the owner acting directly). An app is never the owner acting
        directly, so a forged ``is_user_proxy`` from any other identity is stripped
        to a non-proxy identity before the grant check — it cannot defeat grants.
        """
        identity = self._authorize_identity(_as_identity(agent))
        context = AccessContext(recall_mode="app")
        candidates = self._candidates(query)
        out: list[Any] = []
        for droplet in candidates:
            decision = enforce_grant(
                droplet,
                identity,
                context,
                Operation.READ,
                app_id=self.app_id,
                store=self.store,
                audit=getattr(self.vault, "audit", None),
            )
            if decision.allowed:
                out.append(droplet)
        return out

    def request_access(
        self,
        reservoirs: list[Any],
        operations: list[Any],
        purpose: str,
        expiry: datetime | None = None,
    ) -> Any:
        """File an L4 grant request for broader (cross-reservoir/op) access."""
        req = GrantRequest(
            app_id=self.app_id,
            owner=self.owner,
            reservoirs=[_as_reservoir(r) for r in reservoirs],
            operations=[_as_operation(o) for o in operations],
            purpose=purpose,
            expiry=expiry,
        )
        return self.store.request(req)

    # -- internals ------------------------------------------------------------

    def _authorize_identity(self, identity: AgentIdentity) -> AgentIdentity:
        """Strip a caller-asserted ``is_user_proxy`` unless it is the owner (H2).

        The user-proxy flag is server-assigned and must never be accepted from an
        app: it bypasses the entire grant layer. We honor it only when
        ``identity.name == self.owner`` (the owner acting directly); for any other
        identity we return a non-proxy copy so the grant check applies normally.
        """
        if identity.is_user_proxy and identity.name != self.owner:
            return replace(identity, is_user_proxy=False)
        return identity

    def _candidates(self, query: str) -> list[Any]:
        """Pull candidate droplets from the scoped vault for ``query``.

        Prefers a ``recall``/``search`` method on the vault if present; falls
        back to ``query()``. Tests inject a fake vault exposing one of these.
        """
        for name in ("recall", "search"):
            fn = getattr(self.vault, name, None)
            if callable(fn):
                return list(fn(query))
        query_fn = getattr(self.vault, "query", None)
        if callable(query_fn):
            return list(query_fn())
        return []


def _as_identity(agent: Any) -> AgentIdentity:
    if isinstance(agent, AgentIdentity):
        return agent
    ident = getattr(agent, "identity", None)
    if callable(ident):
        return ident()  # type: ignore[no-any-return]
    return AgentIdentity(name=getattr(agent, "name", str(agent)))


def _as_reservoir(value: Any) -> Reservoir:
    from hydromemory.reservoirs import normalize_reservoir

    return normalize_reservoir(value)


def _as_operation(value: Any) -> Operation:
    if isinstance(value, Operation):
        return value
    return Operation(getattr(value, "value", value))


def register_app(engine: Any, app_id: str, owner: str = "user") -> AppMemory:
    """Create an app-scoped memory handle bound to ``engine``.

    Binds the app to:
      * an **app-scoped** vault view (M2). If the engine already exposes a scoped
        :class:`~hydromemory.vault.VaultRepository`, it is used as-is. Otherwise —
        including the server path that sets ``engine.vault = engine.repo`` (a raw,
        *unscoped* repo) — the backing SQLite repo is wrapped in a
        ``VaultRepository(scope=AppScope(app_id=...))`` so app isolation is
        enforced *structurally* (a scope pre-filter), independent of the grant
        check. A non-SQLite test double (no ``_conn``) is used directly.
      * the engine's event bus (``engine.bus``) if present, else a freshly built
        :class:`~hydromemory.bus.bus.EventBus` bound to the app's vault as its
        permission-gate repo (H1) — a repo-less bus would fail closed and drop
        every droplet-bearing event to an identified subscriber.
      * the engine's :class:`GrantStore` (``engine.grant_store``) if present,
        else a fresh in-memory store.

    For unit tests, inject a fake ``engine`` exposing ``vault`` / ``bus`` /
    ``grant_store`` (or construct :class:`AppMemory` directly).
    """
    vault = _scoped_vault_for(engine, app_id)

    bus = getattr(engine, "bus", None)
    if bus is None:
        from hydromemory.bus.bus import EventBus

        # Bind the app's vault as the gate repo so legitimate, access-checked
        # delivery works; without a repo the fail-closed gate drops everything.
        bus = EventBus(repo=vault)

    store = getattr(engine, "grant_store", None)
    if store is None:
        import sqlite3

        from hydromemory.platform.grants import GrantStore

        store = GrantStore(sqlite3.connect(":memory:"))

    return AppMemory(app_id=app_id, owner=owner, vault=vault, bus=bus, store=store)


def _scoped_vault_for(engine: Any, app_id: str) -> Any:
    """Resolve an **app-scoped** vault for ``app_id`` (M2: never the unscoped repo).

    * An already-scoped :class:`~hydromemory.vault.VaultRepository` (or any object
      exposing an :class:`~hydromemory.vault.AppScope` ``scope``) is returned
      unchanged.
    * Otherwise the backing SQLite repo (``engine.vault`` or ``engine.repo`` if
      they expose a ``_conn``, else ``engine`` itself) is wrapped in a
      ``VaultRepository`` under ``AppScope(app_id=...)`` so the L1 scope filter is
      applied before any grant check.
    * A non-SQLite repository (a test double with no ``_conn``) is returned as-is;
      such fakes stand in as the caller's explicit scoped view.
    """
    from hydromemory.vault import AppScope, VaultRepository

    candidate = getattr(engine, "vault", None)
    # Already a scoped vault view -> trust it.
    if isinstance(candidate, VaultRepository) or isinstance(getattr(candidate, "scope", None), AppScope):
        return candidate

    # Find a SQLite-backed repo to wrap (prefer engine.vault, then engine.repo,
    # then the engine itself); a fake without a ``_conn`` is used directly.
    backing = candidate if candidate is not None else getattr(engine, "repo", None) or engine
    if getattr(backing, "_conn", None) is None:
        return backing

    from hydromemory.governance import AgentIdentity, TrustLevel
    from hydromemory.vault.audit import AuditLog
    from hydromemory.vault.cipher import NullCipher

    return VaultRepository(
        backing,
        NullCipher(),
        AuditLog(backing._conn),
        identity=AgentIdentity(name=app_id, trust_level=TrustLevel.APPROVED),
        scope=AppScope(app_id=app_id),
    )
