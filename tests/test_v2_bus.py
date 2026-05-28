"""v2 Phase B1 — memory event bus tests (PRD §9).

Covers the real :class:`EventBus` (sync fan-out, predicate + permission filtering,
error isolation, cascade-depth guard, sync-callable vs ``asyncio.Queue``
handlers), the :class:`BusAgentRuntime` (agents driven by events), the verb/
pipeline emission seam (events arrive with NO running event loop), and that v1
behaviour is unchanged when no emitter is wired (``NULL_EMITTER``).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from hydromemory.agents.base import AgentContext, BaseAgent
from hydromemory.bus.bus import NULL_BUS, EventBus, NullEventBus, Subscription
from hydromemory.bus.emit import Emitter
from hydromemory.bus.events import EventType, MemoryEvent
from hydromemory.bus.runtime import (
    BusAgentRuntime,
    bus_runtime_from_engine,
    topics_for_stages,
)
from hydromemory.config import HydroConfig
from hydromemory.governance import (
    AccessContext,
    AgentIdentity,
    TrustLevel,
    check_access,
)
from hydromemory.intelligence import build_intelligence
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, Visibility
from hydromemory.verbs import Verbs


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeRepo:
    """Tiny in-memory DropletRepository stand-in (only what the verbs touch)."""

    def __init__(self, seed: dict[str, Droplet] | None = None) -> None:
        self.store: dict[str, Droplet] = dict(seed or {})
        self.links: list[tuple[str, str, str]] = []

    def upsert(self, droplet: Droplet) -> None:
        self.store[droplet.id] = droplet

    def get(self, droplet_id: str) -> Droplet | None:
        return self.store.get(droplet_id)

    def delete(self, droplet_id: str) -> None:
        self.store.pop(droplet_id, None)

    def add_link(self, src_id: str, kind: str, dst_id: str) -> None:
        self.links.append((src_id, kind, dst_id))

    def touch_cycle(self, droplet_id: str, **kwargs: Any) -> None:
        return None

    def search_similar(
        self,
        embedding: Sequence[float],
        k: int = 10,
        candidate_filter: Callable[[Droplet], bool] | None = None,
    ) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for did, d in self.store.items():
            if candidate_filter is not None and not candidate_filter(d):
                continue
            out.append((did, 0.9))
        return out[:k]


class Collector:
    """A sync handler that records every event it receives."""

    def __init__(self) -> None:
        self.events: list[MemoryEvent] = []

    def __call__(self, event: MemoryEvent) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.events]


def _stub_intelligence() -> Any:
    return build_intelligence(HydroConfig(intelligence_backend="stub"))


def _ocean_droplet(did: str = "mem_ocean") -> Droplet:
    """A restricted (ocean / high-trust-only) private droplet."""
    d = Droplet(id=did, content="deep collective knowledge", reservoir=Reservoir.OCEAN)
    d.permissions.visibility = Visibility.PRIVATE
    return d


# --------------------------------------------------------------------------- #
# Pub/sub by topic
# --------------------------------------------------------------------------- #
def test_publish_delivers_to_topic_subscriber_only():
    bus = EventBus()
    absorbed = Collector()
    recalled = Collector()
    bus.subscribe(topics=frozenset({EventType.ABSORBED.value}), handler=absorbed)
    bus.subscribe(topics=frozenset({EventType.RECALLED.value}), handler=recalled)

    n = bus.publish(MemoryEvent(type=EventType.ABSORBED.value))

    assert n == 1
    assert absorbed.types == [EventType.ABSORBED.value]
    assert recalled.events == []


def test_subscribe_none_topics_receives_all():
    bus = EventBus()
    everything = Collector()
    bus.subscribe(topics=None, handler=everything)

    bus.publish(MemoryEvent(type=EventType.ABSORBED.value))
    bus.publish(MemoryEvent(type=EventType.FORGOTTEN.value))

    assert everything.types == [EventType.ABSORBED.value, EventType.FORGOTTEN.value]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    c = Collector()
    sub = bus.subscribe(topics=None, handler=c)
    bus.publish(MemoryEvent(type=EventType.FLOWED.value))
    bus.unsubscribe(sub)
    bus.publish(MemoryEvent(type=EventType.FLOWED.value))
    assert len(c.events) == 1


def test_subscription_has_unique_ids():
    bus = EventBus()
    s1 = bus.subscribe(topics=None, handler=Collector())
    s2 = bus.subscribe(topics=None, handler=Collector())
    assert s1.id != s2.id
    assert isinstance(s1, Subscription)


# --------------------------------------------------------------------------- #
# Predicate filter
# --------------------------------------------------------------------------- #
def test_predicate_filters_events():
    bus = EventBus()
    c = Collector()
    bus.subscribe(
        topics=None,
        predicate=lambda e: e.payload.get("reservoir") == "sacred",
        handler=c,
    )
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, payload={"reservoir": "sacred"}))
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, payload={"reservoir": "working_stream"}))
    assert len(c.events) == 1
    assert c.events[0].payload["reservoir"] == "sacred"


def test_raising_predicate_is_treated_as_no_match():
    bus = EventBus()
    c = Collector()

    def boom(_e: MemoryEvent) -> bool:
        raise RuntimeError("predicate blew up")

    bus.subscribe(topics=None, predicate=boom, handler=c)
    # A raising predicate must not raise out of publish, and delivers nothing.
    assert bus.publish(MemoryEvent(type=EventType.ABSORBED.value)) == 0
    assert c.events == []


# --------------------------------------------------------------------------- #
# Permission-gated delivery
# --------------------------------------------------------------------------- #
def test_permission_gate_only_reaches_high_trust_subscriber():
    repo = FakeRepo({"mem_ocean": _ocean_droplet()})
    bus = EventBus(repo=repo, check_access=check_access)

    low = Collector()
    high = Collector()
    anon = Collector()
    bus.subscribe(
        topics=None,
        handler=low,
        subscriber=AgentIdentity(name="low", trust_level=TrustLevel.SESSION),
    )
    bus.subscribe(
        topics=None,
        handler=high,
        subscriber=AgentIdentity(name="high", trust_level=TrustLevel.HIGH_TRUST),
    )
    # Anonymous subscriber (no identity) gets topic-only delivery (no gate).
    bus.subscribe(topics=None, handler=anon, subscriber=None)

    delivered = bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_ocean"))

    assert high.types == [EventType.ABSORBED.value]  # high-trust passes the gate
    assert low.events == []  # session trust denied on the ocean reservoir
    assert anon.types == [EventType.ABSORBED.value]  # anonymous -> topic-only
    assert delivered == 2


def test_permission_gate_coerces_app_id_string_to_session_identity():
    repo = FakeRepo({"mem_ocean": _ocean_droplet()})
    bus = EventBus(repo=repo, check_access=check_access)
    app = Collector()
    # A bare app-id string subscriber -> AgentIdentity(name=..., SESSION) -> denied on ocean.
    bus.subscribe(topics=None, handler=app, subscriber="some_app")
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_ocean"))
    assert app.events == []


def test_no_droplet_id_skips_permission_gate():
    repo = FakeRepo()
    bus = EventBus(repo=repo, check_access=check_access)
    c = Collector()
    bus.subscribe(
        topics=None,
        handler=c,
        subscriber=AgentIdentity(name="low", trust_level=TrustLevel.SESSION),
    )
    # No droplet_id => topic-only delivery even for a low-trust subscriber.
    bus.publish(MemoryEvent(type=EventType.FORGOTTEN.value, droplet_id=None))
    assert c.types == [EventType.FORGOTTEN.value]


def test_missing_droplet_denies_identified_subscriber_fail_closed():
    # H1 fail-closed: an event names a droplet_id the repo can't load. We cannot
    # prove the subscriber's READ, so an *identified* subscriber is DENIED (no
    # leaking the droplet's existence) — even a topic-only-looking event.
    repo = FakeRepo()  # empty: get() returns None
    bus = EventBus(repo=repo, check_access=check_access)
    identified = Collector()
    anon = Collector()
    bus.subscribe(
        topics=None,
        handler=identified,
        subscriber=AgentIdentity(name="low", trust_level=TrustLevel.SESSION),
    )
    # An anonymous (identity-less) subscriber still gets topic-only delivery.
    bus.subscribe(topics=None, handler=anon, subscriber=None)
    delivered = bus.publish(MemoryEvent(type=EventType.FORGOTTEN.value, droplet_id="gone"))
    assert identified.events == []  # ungateable droplet -> denied for an identity
    assert anon.types == [EventType.FORGOTTEN.value]  # anonymous -> topic-only
    assert delivered == 1


def test_repoless_bus_denies_droplet_event_to_identity_but_delivers_topic_only():
    # H1 core: the DEFAULT (repo-less) bus must fail CLOSED. A droplet-bearing
    # event is denied to an identified subscriber (the gate can't load the
    # droplet), but a topic-only event (no droplet_id) is still delivered, and an
    # anonymous subscriber still receives the droplet-bearing event topic-only.
    bus = EventBus()  # no repo -> nothing can be gated
    identified = Collector()
    anon = Collector()
    bus.subscribe(
        topics=None,
        handler=identified,
        subscriber=AgentIdentity(name="agent", trust_level=TrustLevel.HIGH_TRUST),
    )
    bus.subscribe(topics=None, handler=anon, subscriber=None)

    # (1) Droplet-bearing event: identified subscriber denied, anonymous gets it.
    n_droplet = bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_x"))
    assert identified.events == []  # fail-closed: no repo -> deny identity
    assert anon.types == [EventType.ABSORBED.value]
    assert n_droplet == 1

    # (2) Topic-only event (no droplet_id): both subscribers receive it.
    n_topic = bus.publish(MemoryEvent(type=EventType.FLOWED.value, droplet_id=None))
    assert identified.types == [EventType.FLOWED.value]
    assert anon.types == [EventType.ABSORBED.value, EventType.FLOWED.value]
    assert n_topic == 2


def test_non_access_decision_check_access_denies_fail_closed():
    # L5: an injected check_access that returns a non-AccessDecision (no
    # ``.allowed``) must be treated as DENY, not coerced truthy (fail-open).
    repo = FakeRepo({"mem_ocean": _ocean_droplet()})
    bus = EventBus(repo=repo, check_access=lambda *a, **k: "definitely-not-a-decision")
    c = Collector()
    bus.subscribe(
        topics=None,
        handler=c,
        subscriber=AgentIdentity(name="agent", trust_level=TrustLevel.HIGH_TRUST),
    )
    assert bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_ocean")) == 0
    assert c.events == []


# --------------------------------------------------------------------------- #
# Error isolation
# --------------------------------------------------------------------------- #
def test_handler_error_does_not_block_other_deliveries():
    bus = EventBus()
    good_before = Collector()
    good_after = Collector()

    def raiser(_e: MemoryEvent) -> None:
        raise ValueError("handler failure")

    bus.subscribe(topics=None, handler=good_before)
    bus.subscribe(topics=None, handler=raiser)
    bus.subscribe(topics=None, handler=good_after)

    delivered = bus.publish(MemoryEvent(type=EventType.ABSORBED.value))

    # The raising handler is isolated; both good handlers still receive it.
    assert good_before.types == [EventType.ABSORBED.value]
    assert good_after.types == [EventType.ABSORBED.value]
    # Delivered count reflects only successful deliveries (raiser excluded).
    assert delivered == 2


# --------------------------------------------------------------------------- #
# Cascade / re-entrancy depth guard
# --------------------------------------------------------------------------- #
def test_cascade_depth_guard_stops_event_storm():
    bus = EventBus(max_depth=1)
    seen: list[str] = []

    def cascader(event: MemoryEvent) -> None:
        seen.append(event.type)
        # Re-publish from within a handler (a cascade). With the L4 fix the guard
        # is ``>=``, so at the default max_depth=1 this nested publish is dropped.
        bus.publish(MemoryEvent(type=EventType.TRANSFORMED.value))

    bus.subscribe(topics=None, handler=cascader)
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value))

    # max_depth=1 -> exactly the top-level publish is delivered; the handler's
    # nested publish (depth 1 >= max_depth 1) is dropped. Exact count, not "<=".
    assert seen == [EventType.ABSORBED.value]


def test_cascade_guard_allows_independent_sequential_publishes():
    bus = EventBus(max_depth=1)
    c = Collector()
    bus.subscribe(topics=None, handler=c)
    # Sequential (non-nested) publishes are each at depth 1 -> all delivered.
    for _ in range(5):
        bus.publish(MemoryEvent(type=EventType.FLOWED.value))
    assert len(c.events) == 5


# --------------------------------------------------------------------------- #
# asyncio.Queue handler (the B2 WebSocket seam)
# --------------------------------------------------------------------------- #
def test_queue_handler_put_nowait_and_drop_oldest():
    bus = EventBus()
    queue: asyncio.Queue[MemoryEvent] = asyncio.Queue(maxsize=2)
    bus.subscribe(topics=None, handler=queue)

    # Fill beyond capacity; publish must never block (drop-oldest on Full).
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, payload={"n": 1}))
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, payload={"n": 2}))
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, payload={"n": 3}))

    assert queue.qsize() == 2
    first = queue.get_nowait()
    second = queue.get_nowait()
    # Oldest (n=1) was dropped; the two most-recent remain in FIFO order.
    assert [first.payload["n"], second.payload["n"]] == [2, 3]


def test_queue_handler_delivery_counts_as_delivered():
    bus = EventBus()
    queue: asyncio.Queue[MemoryEvent] = asyncio.Queue()
    bus.subscribe(topics=frozenset({EventType.RECALLED.value}), handler=queue)
    n = bus.publish(MemoryEvent(type=EventType.RECALLED.value))
    assert n == 1
    assert queue.qsize() == 1


# --------------------------------------------------------------------------- #
# apublish convenience
# --------------------------------------------------------------------------- #
def test_apublish_runs_sync_publish():
    bus = EventBus()
    c = Collector()
    bus.subscribe(topics=None, handler=c)

    async def go() -> int:
        return await bus.apublish(MemoryEvent(type=EventType.ABSORBED.value))

    n = asyncio.run(go())
    assert n == 1
    assert c.types == [EventType.ABSORBED.value]


# --------------------------------------------------------------------------- #
# The sync-emit path: a verb emits with NO running event loop
# --------------------------------------------------------------------------- #
def test_absorb_emits_absorbed_event_with_no_event_loop():
    # Sanity: there is no running event loop in this synchronous test.
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    assert running is False

    bus = EventBus()
    c = Collector()
    bus.subscribe(topics=frozenset({EventType.ABSORBED.value}), handler=c)

    verbs = Verbs(repo=FakeRepo(), intelligence=_stub_intelligence(), emit=Emitter(bus))
    droplet = verbs.absorb("the sky is blue today")

    assert c.types == [EventType.ABSORBED.value]
    assert c.events[0].droplet_id == droplet.id
    assert c.events[0].payload["phase"] == droplet.phase.value


def test_freeze_denied_path_emits_nothing():
    # FREEZE on a sacred droplet is policy-denied (overwrite blocked) -> no FROZEN.
    droplet = Droplet(id="mem_sacred", reservoir=Reservoir.SACRED)
    bus = EventBus()
    c = Collector()
    bus.subscribe(topics=None, handler=c)

    agent = AgentIdentity(name="assistant", trust_level=TrustLevel.HIGH_TRUST)
    verbs = Verbs(
        repo=FakeRepo({"mem_sacred": droplet}),
        intelligence=_stub_intelligence(),
        check_access=check_access,
        emit=Emitter(bus),
    )
    out = verbs.freeze(droplet, agent=agent, context=AccessContext())
    assert "freeze_denied" in out.meta
    assert c.events == []  # denied path emits nothing


def test_precipitate_emits_recalled_per_result():
    seed = {"mem_a": Droplet(id="mem_a", content="alpha note", embedding=[0.1])}
    bus = EventBus()
    c = Collector()
    bus.subscribe(topics=frozenset({EventType.RECALLED.value}), handler=c)

    verbs = Verbs(repo=FakeRepo(seed), intelligence=_stub_intelligence(), emit=Emitter(bus))
    resp = verbs.precipitate("alpha", agent=AgentIdentity(name="assistant"))
    # One RECALLED per surfaced result.
    assert len(c.events) == len(resp.result)
    if c.events:
        assert c.events[0].type == EventType.RECALLED.value


# --------------------------------------------------------------------------- #
# BusAgentRuntime drives agents on events
# --------------------------------------------------------------------------- #
class _RecordingAgent(BaseAgent):
    name = "recorder"
    trust_level = TrustLevel.HIGH_TRUST
    stages = ("capture",)

    def __init__(self) -> None:
        super().__init__(engine=None)
        self.seen: list[MemoryEvent] = []

    def run(self, ctx: AgentContext) -> dict[str, Any]:
        event = ctx.payload["event"]
        self.seen.append(event)
        return {"stage": ctx.stage, "type": event.type}


class _AllStagesAgent(BaseAgent):
    name = "all"
    trust_level = TrustLevel.HIGH_TRUST
    stages = ()  # every topic

    def __init__(self) -> None:
        super().__init__(engine=None)
        self.count = 0

    def run(self, ctx: AgentContext) -> int:
        self.count += 1
        return self.count


class _BoomAgent(BaseAgent):
    name = "boom"
    trust_level = TrustLevel.HIGH_TRUST
    stages = ("capture",)

    def __init__(self) -> None:
        super().__init__(engine=None)

    def run(self, ctx: AgentContext) -> Any:
        raise RuntimeError("agent failure")


def test_bus_runtime_drives_agent_on_matching_event():
    bus = EventBus()
    rec = _RecordingAgent()
    runtime = BusAgentRuntime(bus, [rec])

    # capture-stage agent reacts to ABSORBED, ignores RECALLED.
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id=None))
    bus.publish(MemoryEvent(type=EventType.RECALLED.value, droplet_id=None))

    assert [e.type for e in rec.seen] == [EventType.ABSORBED.value]
    assert runtime.last_results["recorder"] == {"stage": "capture", "type": EventType.ABSORBED.value}
    assert runtime.handled["recorder"] == 1


def test_bus_runtime_empty_stages_subscribes_all():
    bus = EventBus()
    a = _AllStagesAgent()
    BusAgentRuntime(bus, [a])
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value))
    bus.publish(MemoryEvent(type=EventType.DISTILLED.value))
    assert a.count == 2


def test_bus_runtime_isolates_failing_agent():
    bus = EventBus()
    boom = _BoomAgent()
    rec = _RecordingAgent()
    runtime = BusAgentRuntime(bus, [boom, rec])

    # boom raises, but rec (registered after) still receives the event.
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value))

    assert [e.type for e in rec.seen] == [EventType.ABSORBED.value]
    assert "error" in runtime.last_results["boom"]


def test_bus_runtime_permission_gate_applies_to_agents():
    # A low-trust agent subscribed to all topics is gated off an ocean droplet.
    repo = FakeRepo({"mem_ocean": _ocean_droplet()})
    bus = EventBus(repo=repo, check_access=check_access)

    class _LowAgent(BaseAgent):
        name = "low"
        trust_level = TrustLevel.SESSION
        stages = ()

        def __init__(self) -> None:
            super().__init__(engine=None)
            self.count = 0

        def run(self, ctx: AgentContext) -> int:
            self.count += 1
            return self.count

    low = _LowAgent()
    BusAgentRuntime(bus, [low])
    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="mem_ocean"))
    assert low.count == 0  # denied by the permission gate


def test_bus_runtime_from_engine_subscribes_eight_roles():
    bus = EventBus()
    runtime = bus_runtime_from_engine(engine=object(), bus=bus)
    assert len(runtime.agents) == 8
    names = {a.name for a in runtime.agents}
    assert names == {
        "capture",
        "hydrologist",
        "filtration",
        "privacy",
        "recall",
        "reflection",
        "distillation",
        "archivist",
    }


def test_topics_for_stages_mapping():
    # Empty stages -> all topics (None).
    assert topics_for_stages(()) is None
    # capture stage -> just ABSORBED.
    assert topics_for_stages(("capture",)) == frozenset({EventType.ABSORBED.value})
    # recall stage -> RECALLED.
    assert topics_for_stages(("recall",)) == frozenset({EventType.RECALLED.value})
    # Unknown stage contributes no topics (empty, never fires).
    assert topics_for_stages(("nonsense",)) == frozenset()


# --------------------------------------------------------------------------- #
# v1 unchanged: Verbs without an emitter still works (NULL_EMITTER no-op)
# --------------------------------------------------------------------------- #
def test_verbs_without_emit_still_works_and_is_event_free():
    # No emit= argument -> defaults to NULL_EMITTER (publishes to NULL_BUS).
    verbs = Verbs(repo=FakeRepo(), intelligence=_stub_intelligence())
    droplet = verbs.absorb("a plain memory")
    assert droplet.phase is Phase.LIQUID
    # NULL_EMITTER -> NULL_BUS.publish returns 0 and nothing is observable.
    assert verbs.emit.bus is NULL_BUS
    assert NULL_BUS.publish(MemoryEvent(type=EventType.ABSORBED.value)) == 0


def test_null_event_bus_subscribe_is_inactive():
    sub = NULL_BUS.subscribe(topics=None, handler=Collector())
    assert sub.active is False
    assert NULL_BUS.publish(MemoryEvent(type=EventType.FLOWED.value)) == 0
    assert isinstance(NULL_BUS, NullEventBus)


# --------------------------------------------------------------------------- #
# Real SqliteDropletRepository on a temp db (permission gate, end-to-end)
# --------------------------------------------------------------------------- #
def test_permission_gate_with_real_sqlite_repo(tmp_path):
    from hydromemory.storage.sqlite_repository import SqliteDropletRepository

    config = HydroConfig(db_path=str(tmp_path / "bus_test.db"), intelligence_backend="stub")
    repo = SqliteDropletRepository(config)
    try:
        droplet = _ocean_droplet("mem_real_ocean")
        repo.upsert(droplet)

        bus = EventBus(repo=repo, check_access=check_access)
        high = Collector()
        low = Collector()
        bus.subscribe(
            topics=None,
            handler=high,
            subscriber=AgentIdentity(name="curator", trust_level=TrustLevel.HIGH_TRUST),
        )
        bus.subscribe(
            topics=None,
            handler=low,
            subscriber=AgentIdentity(name="guest", trust_level=TrustLevel.SESSION),
        )
        bus.publish(MemoryEvent(type=EventType.RECALLED.value, droplet_id="mem_real_ocean"))

        assert high.types == [EventType.RECALLED.value]
        assert low.events == []
    finally:
        repo.close()
