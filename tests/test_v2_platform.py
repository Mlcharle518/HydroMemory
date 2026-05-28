"""v2 Phase B1 — Platform track tests (L1 apps / L3 mesh / L4 grants).

These exercise the platform layer in isolation: the bus, vault, audit, and
engine are all faked here so the suite does not depend on the concurrent Bus /
Vault tracks. The grant table uses a temp/in-memory sqlite db.

Coverage:
  * enforce_grant — narrow-only composition (a denied check_access stays denied
    even with a grant; an allowed base + no grant denies a non-owner app; an
    allowed base + active grant allows; owner / user-proxy bypass the grant).
  * GrantStore — request→PENDING, approve→APPROVED (active), revoke→denied,
    expired grant→denied; persistence across a reopen of the same db file.
  * Mesh — an ABSORBED event on a contaminated droplet drives the filtration
    reaction (assess_and_route) under MUTATE; the cascade depth guard stops at
    ``max_depth`` and the per-cycle dedupe prevents a second fire.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hydromemory.agents.filtration import FiltrationAgent
from hydromemory.bus.bus import EventBus, Subscription
from hydromemory.bus.events import EventType, MemoryEvent
from hydromemory.governance import (
    AccessContext,
    AgentIdentity,
    Operation,
    TrustLevel,
)
from hydromemory.platform.apps import AppMemory, register_app
from hydromemory.platform.grants import (
    GrantRequest,
    GrantStatus,
    GrantStore,
    enforce_grant,
)
from hydromemory.platform.mesh import DEFAULT_REACTIONS, Mesh, Reaction
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Permissions, Phase, new_id

# --------------------------------------------------------------------------- #
# Test doubles                                                                #
# --------------------------------------------------------------------------- #


class FakeBus(EventBus):
    """A synchronous in-process bus that dispatches by topic to handlers.

    Records every published event so cascade depth can be asserted.
    """

    def __init__(self) -> None:
        self.subs: list[Subscription] = []
        self.published: list[MemoryEvent] = []

    def publish(self, event: MemoryEvent) -> int:
        self.published.append(event)
        delivered = 0
        for sub in list(self.subs):
            if not sub.active:
                continue
            if sub.topics is not None and event.type not in sub.topics:
                continue
            if sub.predicate is not None and not sub.predicate(event):
                continue
            sub.handler(event)
            delivered += 1
        return delivered

    def subscribe(
        self,
        *,
        topics: frozenset[str] | None = None,
        predicate: Callable[[MemoryEvent], bool] | None = None,
        handler: Any,
        subscriber: Any = None,
    ) -> Subscription:
        sub = Subscription(
            id=new_id(),
            topics=topics,
            predicate=predicate,
            handler=handler,
            subscriber=subscriber,
        )
        self.subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.active = False


class FakeVault:
    """An in-memory droplet store with the slice of the repo the mesh uses."""

    def __init__(self, droplets: list[Droplet] | None = None) -> None:
        self.store: dict[str, Droplet] = {d.id: d for d in (droplets or [])}
        self.upserts: list[Droplet] = []
        self.audit = None

    def get(self, droplet_id: str) -> Droplet | None:
        return self.store.get(droplet_id)

    def upsert(self, droplet: Droplet) -> None:
        self.store[droplet.id] = droplet
        self.upserts.append(droplet)

    def recall(self, query: str) -> list[Droplet]:
        return list(self.store.values())


class StubFiltrationEngine:
    """Engine surface the FiltrationAgent uses; records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def assess_and_route(self, droplet: Droplet, context: dict) -> Droplet:
        self.calls.append(("assess_and_route", droplet.id))
        # Route to contaminated + mark unusable: a real, observable change.
        routed = Droplet.from_dict(droplet.to_dict())
        routed.reservoir = Reservoir.CONTAMINATED
        routed.phase = Phase.POLLUTED
        routed.meta = {**droplet.meta, "usable_for_generation": False}
        return routed

    def filter(self, droplet: Droplet) -> Droplet:
        self.calls.append(("filter", droplet.id))
        filtered = Droplet.from_dict(droplet.to_dict())
        filtered.phase = Phase.FILTERED
        filtered.reservoir = Reservoir.SURFACE
        return filtered


class StubRuntime:
    """Minimal runtime exposing ``.agents`` and ``.register`` for the mesh."""

    def __init__(self, agents: list[Any]) -> None:
        self._agents = list(agents)

    @property
    def agents(self) -> tuple[Any, ...]:
        return tuple(self._agents)

    def register(self, agent: Any) -> Any:
        self._agents.append(agent)
        return agent


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def store() -> GrantStore:
    return GrantStore(sqlite3.connect(":memory:"))


def _surface_droplet(owner: str = "user", **kw: Any) -> Droplet:
    """A plain user-visible surface droplet (base check_access allows READ)."""
    return Droplet(
        id=kw.pop("id", None) or new_id(),
        content=kw.pop("content", "hello"),
        reservoir=Reservoir.SURFACE,
        permissions=Permissions(owner=owner),
        **kw,
    )


def _approved_grant(
    store: GrantStore,
    app_id: str,
    owner: str,
    reservoirs: list[Reservoir],
    operations: list[Operation],
    *,
    expiry: datetime | None = None,
) -> str:
    req = GrantRequest(
        app_id=app_id,
        owner=owner,
        reservoirs=reservoirs,
        operations=operations,
        purpose="test",
        expiry=expiry,
    )
    store.request(req)
    store.approve(req.request_id, owner)
    return req.request_id


# --------------------------------------------------------------------------- #
# enforce_grant — narrow-only composition                                     #
# --------------------------------------------------------------------------- #

APP_AGENT = AgentIdentity(name="calendar_app", trust_level=TrustLevel.APPROVED)
# The owner acting directly (user proxy). High trust so check_access's trust
# floor is cleared — the grant-layer bypass is then what we exercise.
OWNER_AGENT = AgentIdentity(
    name="owner", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True
)


def test_enforce_grant_denied_check_access_stays_denied(store: GrantStore) -> None:
    # A contaminated droplet is filtration-agent-only: check_access denies a
    # non-filtration app even for READ. A grant must NOT resurrect that.
    droplet = _surface_droplet()
    droplet.reservoir = Reservoir.CONTAMINATED
    _approved_grant(
        store, "calendar_app", "user", [Reservoir.CONTAMINATED], [Operation.READ]
    )
    decision = enforce_grant(
        droplet,
        APP_AGENT,
        AccessContext(),
        Operation.READ,
        app_id="calendar_app",
        store=store,
    )
    assert decision.allowed is False
    # The denial reason is the governance one, not a grant denial.
    assert "filtration" in (decision.denial_reason or "")


def test_enforce_grant_allowed_base_no_grant_denies_non_owner(store: GrantStore) -> None:
    droplet = _surface_droplet()
    decision = enforce_grant(
        droplet,
        APP_AGENT,
        AccessContext(),
        Operation.READ,
        app_id="calendar_app",
        store=store,
    )
    assert decision.allowed is False
    assert "no active grant" in (decision.denial_reason or "")


def test_enforce_grant_allowed_base_with_active_grant_allows(store: GrantStore) -> None:
    droplet = _surface_droplet()
    _approved_grant(
        store, "calendar_app", "user", [Reservoir.SURFACE], [Operation.READ]
    )
    decision = enforce_grant(
        droplet,
        APP_AGENT,
        AccessContext(),
        Operation.READ,
        app_id="calendar_app",
        store=store,
    )
    assert decision.allowed is True


def test_enforce_grant_owner_user_proxy_bypasses_grant(store: GrantStore) -> None:
    # No grant exists, yet the owner (user proxy) is allowed: grants are an
    # app-layer concern; the owner operates at L2.
    droplet = _surface_droplet()
    decision = enforce_grant(
        droplet,
        OWNER_AGENT,
        AccessContext(),
        Operation.READ,
        app_id=None,
        store=store,
    )
    assert decision.allowed is True


def test_enforce_grant_wrong_operation_in_grant_denies(store: GrantStore) -> None:
    # Grant covers READ but the request is MUTATE -> narrow to deny.
    droplet = _surface_droplet()
    _approved_grant(
        store, "calendar_app", "user", [Reservoir.SURFACE], [Operation.READ]
    )
    decision = enforce_grant(
        droplet,
        APP_AGENT,
        AccessContext(),
        Operation.MUTATE,
        app_id="calendar_app",
        store=store,
    )
    assert decision.allowed is False


def test_enforce_grant_wrong_reservoir_in_grant_denies(store: GrantStore) -> None:
    # Grant covers GROUNDWATER but the droplet is SURFACE -> deny.
    droplet = _surface_droplet()
    _approved_grant(
        store, "calendar_app", "user", [Reservoir.GROUNDWATER], [Operation.READ]
    )
    decision = enforce_grant(
        droplet,
        APP_AGENT,
        AccessContext(),
        Operation.READ,
        app_id="calendar_app",
        store=store,
    )
    assert decision.allowed is False


def test_enforce_grant_grant_for_different_owner_denies(store: GrantStore) -> None:
    # Droplet owned by "alice" but the grant was approved by "user".
    droplet = _surface_droplet(owner="alice")
    _approved_grant(
        store, "calendar_app", "user", [Reservoir.SURFACE], [Operation.READ]
    )
    decision = enforce_grant(
        droplet,
        APP_AGENT,
        AccessContext(),
        Operation.READ,
        app_id="calendar_app",
        store=store,
    )
    assert decision.allowed is False


def test_enforce_grant_writes_audit_on_allow(store: GrantStore) -> None:
    droplet = _surface_droplet()
    _approved_grant(
        store, "calendar_app", "user", [Reservoir.SURFACE], [Operation.READ]
    )
    audit = _RecordingAudit()
    decision = enforce_grant(
        droplet,
        APP_AGENT,
        AccessContext(),
        Operation.READ,
        app_id="calendar_app",
        store=store,
        audit=audit,
    )
    assert decision.allowed is True
    assert len(audit.entries) == 1
    assert audit.entries[0]["allowed"] is True
    assert audit.entries[0]["operation"] == "read"


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, *, actor, app_id, operation, droplet_id, decision, detail=None):
        self.entries.append(
            {
                "actor": actor,
                "app_id": app_id,
                "operation": operation,
                "droplet_id": droplet_id,
                "allowed": decision.allowed,
                "detail": detail,
            }
        )
        return None


# --------------------------------------------------------------------------- #
# GrantStore — lifecycle + persistence                                        #
# --------------------------------------------------------------------------- #


def test_grant_request_is_pending(store: GrantStore) -> None:
    req = GrantRequest(
        app_id="a", owner="user", reservoirs=[Reservoir.SURFACE],
        operations=[Operation.READ], purpose="p",
    )
    grant = store.request(req)
    assert grant.status is GrantStatus.PENDING
    assert grant.granted_at is None
    # Not active until approved.
    assert store.active_for("a") == []


def test_grant_approve_makes_it_active(store: GrantStore) -> None:
    rid = _approved_grant(store, "a", "user", [Reservoir.SURFACE], [Operation.READ])
    active = store.active_for("a")
    assert [g.request_id for g in active] == [rid]
    assert active[0].status is GrantStatus.APPROVED
    assert active[0].granted_at is not None
    assert active[0].reservoirs == frozenset({Reservoir.SURFACE})
    assert active[0].operations == frozenset({Operation.READ})


def test_grant_deny_is_not_active(store: GrantStore) -> None:
    req = GrantRequest(
        app_id="a", owner="user", reservoirs=[Reservoir.SURFACE],
        operations=[Operation.READ], purpose="p",
    )
    store.request(req)
    denied = store.deny(req.request_id, "user")
    assert denied.status is GrantStatus.DENIED
    assert store.active_for("a") == []


def test_grant_revoke_makes_it_inactive(store: GrantStore) -> None:
    rid = _approved_grant(store, "a", "user", [Reservoir.SURFACE], [Operation.READ])
    assert len(store.active_for("a")) == 1
    revoked = store.revoke(rid, "user")
    assert revoked.status is GrantStatus.REVOKED
    assert store.active_for("a") == []


def test_revoked_grant_denies_enforcement(store: GrantStore) -> None:
    droplet = _surface_droplet()
    rid = _approved_grant(store, "calendar_app", "user", [Reservoir.SURFACE], [Operation.READ])
    assert enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store,
    ).allowed is True
    store.revoke(rid, "user")
    assert enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store,
    ).allowed is False


def test_expired_grant_is_not_active(store: GrantStore) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    _approved_grant(
        store, "a", "user", [Reservoir.SURFACE], [Operation.READ], expiry=past
    )
    # Past expiry -> treated as EXPIRED -> excluded from active_for.
    assert store.active_for("a") == []


def test_expired_grant_denies_enforcement(store: GrantStore) -> None:
    droplet = _surface_droplet()
    past = datetime.now(UTC) - timedelta(seconds=1)
    _approved_grant(
        store, "calendar_app", "user", [Reservoir.SURFACE], [Operation.READ], expiry=past
    )
    decision = enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store,
    )
    assert decision.allowed is False


def test_future_expiry_stays_active(store: GrantStore) -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    _approved_grant(
        store, "a", "user", [Reservoir.SURFACE], [Operation.READ], expiry=future
    )
    assert len(store.active_for("a")) == 1


def test_owner_only_transitions(store: GrantStore) -> None:
    req = GrantRequest(
        app_id="a", owner="user", reservoirs=[Reservoir.SURFACE],
        operations=[Operation.READ], purpose="p",
    )
    store.request(req)
    with pytest.raises(PermissionError):
        store.approve(req.request_id, "someone_else")


def test_grant_list_by_owner(store: GrantStore) -> None:
    _approved_grant(store, "a", "user", [Reservoir.SURFACE], [Operation.READ])
    _approved_grant(store, "b", "user", [Reservoir.CLOUD], [Operation.READ])
    _approved_grant(store, "c", "alice", [Reservoir.SURFACE], [Operation.READ])
    assert len(store.list("user")) == 2
    assert len(store.list("alice")) == 1


def test_grant_store_persists_across_reopen(tmp_path) -> None:
    db = str(tmp_path / "grants.db")
    rid = None
    expiry = datetime.now(UTC) + timedelta(days=1)
    # First connection: request + approve, then close.
    conn1 = sqlite3.connect(db)
    store1 = GrantStore(conn1)
    req = GrantRequest(
        app_id="calendar_app", owner="user",
        reservoirs=[Reservoir.SURFACE, Reservoir.CLOUD],
        operations=[Operation.READ, Operation.MUTATE],
        purpose="sync calendar", expiry=expiry,
    )
    store1.request(req)
    rid = req.request_id
    store1.approve(rid, "user")
    conn1.close()

    # Second connection over the same file: the grant must round-trip.
    conn2 = sqlite3.connect(db)
    store2 = GrantStore(conn2)  # idempotent DDL: re-opening must not fail
    active = store2.active_for("calendar_app")
    assert len(active) == 1
    grant = active[0]
    assert grant.request_id == rid
    assert grant.status is GrantStatus.APPROVED
    assert grant.reservoirs == frozenset({Reservoir.SURFACE, Reservoir.CLOUD})
    assert grant.operations == frozenset({Operation.READ, Operation.MUTATE})
    assert grant.purpose == "sync calendar"
    assert grant.granted_at is not None
    assert grant.expiry is not None
    conn2.close()


# --------------------------------------------------------------------------- #
# Mesh — reaction + cascade safety                                            #
# --------------------------------------------------------------------------- #


def _build_mesh(droplet: Droplet, *, max_depth: int = 1) -> tuple[Mesh, FakeBus, FakeVault, StubFiltrationEngine]:
    engine = StubFiltrationEngine()
    agent = FiltrationAgent(engine)
    runtime = StubRuntime([agent])
    bus = FakeBus()
    vault = FakeVault([droplet])
    mesh = Mesh(runtime, bus, vault, max_depth=max_depth)
    mesh.attach()
    return mesh, bus, vault, engine


def test_mesh_attach_subscribes_reactions() -> None:
    droplet = _surface_droplet()
    mesh, bus, _, _ = _build_mesh(droplet)
    # Filtration handles ABSORBED + POLLUTED in the default table; reflection is
    # absent from this runtime, so its DISTILLED reaction is skipped.
    topics = {next(iter(s.topics)) for s in bus.subs if s.topics}
    assert EventType.ABSORBED.value in topics
    assert EventType.POLLUTED.value in topics


def test_mesh_absorbed_drives_filtration_route() -> None:
    # A freshly-absorbed (clean) droplet: ABSORBED -> filtration assess_and_route.
    droplet = _surface_droplet(id="mem_fresh")
    mesh, bus, vault, engine = _build_mesh(droplet, max_depth=1)
    delivered = bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_fresh", payload={"_depth": 0})
    )
    assert delivered >= 1
    # Filtration's assess_and_route ran and the routed droplet was upserted.
    assert ("assess_and_route", "mem_fresh") in engine.calls
    assert any(d.id == "mem_fresh" for d in vault.upserts)
    assert vault.store["mem_fresh"].reservoir is Reservoir.CONTAMINATED


def test_mesh_emits_follow_on_with_incremented_depth() -> None:
    droplet = _surface_droplet(id="mem_fresh")
    mesh, bus, vault, engine = _build_mesh(droplet, max_depth=1)
    bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_fresh", payload={"_depth": 0})
    )
    follow_ons = [e for e in bus.published if e.type == EventType.TRANSFORMED.value]
    assert len(follow_ons) == 1
    assert follow_ons[0].payload["_depth"] == 1


def test_mesh_cascade_depth_guard_stops_at_max_depth() -> None:
    # With max_depth=1, an event already at _depth=1 must NOT fire a reaction.
    droplet = _surface_droplet(id="mem_fresh")
    mesh, bus, vault, engine = _build_mesh(droplet, max_depth=1)
    bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_fresh", payload={"_depth": 1})
    )
    # The subscriber matches the topic but the handler no-ops at the depth
    # guard: no engine call, no upsert.
    assert engine.calls == []
    assert vault.upserts == []


def test_mesh_dedupe_prevents_refire_in_cycle() -> None:
    droplet = _surface_droplet(id="mem_fresh")
    mesh, bus, vault, engine = _build_mesh(droplet, max_depth=1)
    evt = MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_fresh", payload={"_depth": 0})
    bus.publish(evt)
    first = len(engine.calls)
    # Re-publishing the same (type, droplet, agent) within the cycle is a no-op.
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_fresh", payload={"_depth": 0}))
    assert len(engine.calls) == first == 1
    # After reset_cycle, the reaction can fire again.
    mesh.reset_cycle()
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_fresh", payload={"_depth": 0}))
    assert len(engine.calls) == 2


def test_mesh_polluted_drives_filter() -> None:
    # A POLLUTED event on a contaminated droplet -> filtration.filter (TRANSFORM).
    droplet = Droplet(
        id="mem_bad", content="x", reservoir=Reservoir.CONTAMINATED, phase=Phase.POLLUTED,
        permissions=Permissions(owner="user"),
    )
    mesh, bus, vault, engine = _build_mesh(droplet, max_depth=1)
    bus.publish(
        MemoryEvent(type=EventType.POLLUTED.value, droplet_id="mem_bad", payload={"_depth": 0})
    )
    assert ("filter", "mem_bad") in engine.calls
    # The filtered result emits a terminal FILTERED follow-on.
    assert any(e.type == EventType.FILTERED.value for e in bus.published)


def test_mesh_skips_denied_reaction_without_upsert() -> None:
    # A non-filtration agent reacting to a contaminated droplet is denied by
    # check_access (filtration-only); the mesh must SKIP, not upsert.
    class _PlainAgent(FiltrationAgent):
        name = "not_filtration"
        is_filtration = False

    engine = StubFiltrationEngine()
    agent = _PlainAgent(engine)
    runtime = StubRuntime([agent])
    bus = FakeBus()
    droplet = Droplet(
        id="mem_bad", content="x", reservoir=Reservoir.CONTAMINATED, phase=Phase.POLLUTED,
        permissions=Permissions(owner="user"),
    )
    vault = FakeVault([droplet])
    mesh = Mesh(runtime, bus, vault, max_depth=1)
    # Manually register a reaction for this non-filtration agent.
    mesh.register_external(
        agent,
        [Reaction(EventType.POLLUTED, "not_filtration", Operation.TRANSFORM, lambda a, d: d, EventType.FILTERED)],
    )
    bus.publish(
        MemoryEvent(type=EventType.POLLUTED.value, droplet_id="mem_bad", payload={"_depth": 0})
    )
    assert vault.upserts == []  # denied -> nothing applied


def test_mesh_terminal_phase_not_rereacted() -> None:
    # A FILTERED droplet is a finished product; even an ABSORBED event no-ops.
    droplet = _surface_droplet(id="mem_done")
    droplet.phase = Phase.FILTERED
    mesh, bus, vault, engine = _build_mesh(droplet, max_depth=1)
    bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_done", payload={"_depth": 0})
    )
    assert engine.calls == []
    assert vault.upserts == []


def test_mesh_terminal_event_not_reacted() -> None:
    # The mesh ignores FILTERED/ARCHIVED events entirely (no re-reaction).
    droplet = _surface_droplet(id="mem_x")
    mesh, bus, vault, engine = _build_mesh(droplet, max_depth=1)
    mesh._react(DEFAULT_REACTIONS[0], FiltrationAgent(engine),
                MemoryEvent(type=EventType.FILTERED.value, droplet_id="mem_x"))
    assert engine.calls == []


def test_mesh_no_op_proposal_emits_nothing() -> None:
    # If the agent returns an unchanged droplet, nothing is upserted/emitted.
    droplet = _surface_droplet(id="mem_same")

    class _NoOpEngine(StubFiltrationEngine):
        def assess_and_route(self, droplet: Droplet, context: dict) -> Droplet:
            self.calls.append(("assess_and_route", droplet.id))
            return droplet  # unchanged

    engine = _NoOpEngine()
    agent = FiltrationAgent(engine)
    runtime = StubRuntime([agent])
    bus = FakeBus()
    vault = FakeVault([droplet])
    mesh = Mesh(runtime, bus, vault, max_depth=1)
    mesh.attach()
    bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_same", payload={"_depth": 0})
    )
    assert vault.upserts == []
    assert [e for e in bus.published if e.type == EventType.TRANSFORMED.value] == []


# --------------------------------------------------------------------------- #
# AppMemory (L1)                                                              #
# --------------------------------------------------------------------------- #


def test_app_absorb_tags_app_id_and_emits() -> None:
    bus = FakeBus()
    vault = FakeVault()
    store = GrantStore(sqlite3.connect(":memory:"))
    app = AppMemory(app_id="calendar", owner="user", vault=vault, bus=bus, store=store)
    out = app.absorb("a meeting note", reservoir=Reservoir.SURFACE)
    assert out["meta"]["app_id"] == "calendar"
    assert len(vault.upserts) == 1
    # An ABSORBED event was published for the new droplet.
    absorbed = [e for e in bus.published if e.type == EventType.ABSORBED.value]
    assert len(absorbed) == 1
    assert absorbed[0].app_id == "calendar"
    assert absorbed[0].droplet_id == out["id"]


def test_app_recall_enforces_grant_per_candidate() -> None:
    bus = FakeBus()
    # Two surface droplets; the app has a grant -> both readable.
    d1 = _surface_droplet(id="m1")
    d2 = _surface_droplet(id="m2")
    vault = FakeVault([d1, d2])
    store = GrantStore(sqlite3.connect(":memory:"))
    _approved_grant(store, "calendar", "user", [Reservoir.SURFACE], [Operation.READ])
    app = AppMemory(app_id="calendar", owner="user", vault=vault, bus=bus, store=store)
    out = app.recall("anything", APP_AGENT)
    assert {d.id for d in out} == {"m1", "m2"}


def test_app_recall_filters_without_grant() -> None:
    bus = FakeBus()
    vault = FakeVault([_surface_droplet(id="m1")])
    store = GrantStore(sqlite3.connect(":memory:"))  # no grant
    app = AppMemory(app_id="calendar", owner="user", vault=vault, bus=bus, store=store)
    assert app.recall("anything", APP_AGENT) == []


def test_app_recall_owner_proxy_sees_all() -> None:
    bus = FakeBus()
    vault = FakeVault([_surface_droplet(id="m1"), _surface_droplet(id="m2")])
    store = GrantStore(sqlite3.connect(":memory:"))  # no grant needed for owner
    # The owner acting directly: the proxy identity's NAME matches the app owner,
    # so (post-H2) the user-proxy bypass is honored and no grant is needed.
    owner_proxy = AgentIdentity(
        name="user", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True
    )
    app = AppMemory(app_id="calendar", owner="user", vault=vault, bus=bus, store=store)
    out = app.recall("anything", owner_proxy)
    assert {d.id for d in out} == {"m1", "m2"}


def test_app_recall_forged_user_proxy_from_non_owner_does_not_bypass_grant() -> None:
    # H2: a compromised app forges is_user_proxy=True on an identity whose name is
    # NOT the app owner. AppMemory.recall must strip the flag so the L4 grant layer
    # still applies; with no grant present, recall returns nothing (not bypassed).
    bus = FakeBus()
    vault = FakeVault([_surface_droplet(id="m1"), _surface_droplet(id="m2")])
    store = GrantStore(sqlite3.connect(":memory:"))  # no grant
    forged = AgentIdentity(
        name="calendar_app", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True
    )
    app = AppMemory(app_id="calendar", owner="user", vault=vault, bus=bus, store=store)
    # Forged proxy is ignored -> grant layer enforced -> no grant -> empty.
    assert app.recall("anything", forged) == []
    # Sanity: granting SURFACE/READ then lets the same (non-proxy) app through,
    # proving the deny above was the grant layer, not some other gate.
    _approved_grant(store, "calendar", "user", [Reservoir.SURFACE], [Operation.READ])
    assert {d.id for d in app.recall("anything", forged)} == {"m1", "m2"}


def test_app_request_access_creates_pending_grant() -> None:
    bus = FakeBus()
    vault = FakeVault()
    store = GrantStore(sqlite3.connect(":memory:"))
    app = AppMemory(app_id="calendar", owner="user", vault=vault, bus=bus, store=store)
    grant = app.request_access([Reservoir.GROUNDWATER], [Operation.READ], "deep recall")
    assert grant.status is GrantStatus.PENDING
    assert grant.app_id == "calendar"
    assert store.list("user")[0].request_id == grant.request_id


def test_register_app_binds_engine_views() -> None:
    bus = FakeBus()
    vault = FakeVault()
    store = GrantStore(sqlite3.connect(":memory:"))

    class _Engine:
        pass

    engine = _Engine()
    engine.vault = vault  # type: ignore[attr-defined]
    engine.bus = bus  # type: ignore[attr-defined]
    engine.grant_store = store  # type: ignore[attr-defined]
    app = register_app(engine, "calendar", owner="user")
    assert app.vault is vault
    assert app.bus is bus
    assert app.store is store
    assert app.app_id == "calendar"


def test_register_app_defaults_when_engine_bare() -> None:
    # A bare engine with no vault/bus/store: register_app must still build a
    # usable handle (a built EventBus + in-memory grant store, engine as repo).
    class _Bare:
        pass

    app = register_app(_Bare(), "calendar")
    assert app.app_id == "calendar"
    assert app.owner == "user"
    assert app.store is not None
    assert app.bus is not None


def test_register_app_wraps_unscoped_repo_in_app_scope(tmp_path) -> None:
    # M2: when the engine's vault is the raw (unscoped) SQLite repo — exactly the
    # server's ``engine.vault = engine.repo`` fallback — register_app must wrap it
    # in a VaultRepository under AppScope(app_id), so candidates are app-scoped
    # *structurally* (a pre-filter), independent of the grant check.
    from hydromemory.config import HydroConfig
    from hydromemory.storage.sqlite_repository import SqliteDropletRepository
    from hydromemory.vault import VaultRepository

    cfg = HydroConfig(db_path=str(tmp_path / "scope.db"), intelligence_backend="stub")
    repo = SqliteDropletRepository(cfg)
    try:
        class _Engine:
            pass

        engine = _Engine()
        engine.vault = repo  # type: ignore[attr-defined]  # the unscoped raw repo
        engine.repo = repo  # type: ignore[attr-defined]

        cal = register_app(engine, "calendar", owner="user")
        other = register_app(engine, "other", owner="user")
        # The fallback was wrapped, not used raw.
        assert isinstance(cal.vault, VaultRepository)
        assert cal.vault.scope.app_id == "calendar"

        # Absorb one droplet per app (each tags its own app_id column).
        cal.absorb("calendar note", reservoir=Reservoir.SURFACE)
        other.absorb("other note", reservoir=Reservoir.SURFACE)

        # Owner proxy (name matches owner) bypasses grants, isolating SCOPE: the
        # calendar app sees ONLY its own droplet even though both share the repo.
        owner_proxy = AgentIdentity(
            name="user", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True
        )
        contents = {d.content for d in cal.recall("note", owner_proxy)}
        assert contents == {"calendar note"}  # structurally app-scoped
    finally:
        repo.close()
