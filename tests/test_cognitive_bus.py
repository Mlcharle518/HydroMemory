"""Unified HydroCognitive event bus tests (Master Spec §17; ADR-0049).

Covers cross-layer routing by :class:`ObjectType`, the envelope-based fail-closed
permission gate (owner / allowed_agents / public / anonymous), predicate filtering,
the :class:`CognitiveEvent` serialization round-trip, and the standalone
:func:`envelope_allows` gate. Envelopes are constructed directly — no layer objects
are needed, which is the whole point of gating on the §8 envelope.
"""
from __future__ import annotations

from hydromemory.canonical.envelope import (
    CanonicalObject,
    CanonicalPermissions,
    ObjectType,
)
from hydromemory.canonical.verbs import CanonicalVerb
from hydromemory.cognitive_bus import (
    NULL_COGNITIVE_BUS,
    CognitiveBus,
    CognitiveEvent,
    NullCognitiveBus,
    envelope_allows,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _obj(
    object_type: ObjectType,
    *,
    id: str = "obj_1",
    owner: str = "user",
    visibility: str = "private",
    allowed_agents: list[str] | None = None,
) -> CanonicalObject:
    return CanonicalObject(
        id=id,
        object_type=object_type,
        owner=owner,
        permissions=CanonicalPermissions(
            visibility=visibility,
            allowed_agents=list(allowed_agents or []),
        ),
    )


def _event(obj: CanonicalObject, verb: str = "ABSORB") -> CognitiveEvent:
    return CognitiveEvent(object_ref=obj, verb=verb)


class _Collector:
    """A sync handler that records the events it receives."""

    def __init__(self) -> None:
        self.received: list[CognitiveEvent] = []

    def __call__(self, event: CognitiveEvent) -> None:
        self.received.append(event)


# --------------------------------------------------------------------------- #
# (a) Routing by object type
# --------------------------------------------------------------------------- #
def test_typed_subscriber_receives_only_its_type():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(object_types={ObjectType.INTENT}, subscriber="user", handler=sink)

    intent = _event(_obj(ObjectType.INTENT, id="int_1", owner="user"), verb=CanonicalVerb.FORM_INTENT.value)
    memory = _event(_obj(ObjectType.MEMORY, id="mem_1", owner="user"), verb=CanonicalVerb.ABSORB.value)

    assert bus.publish(intent) == 1
    assert bus.publish(memory) == 0
    assert [e.object_id for e in sink.received] == ["int_1"]


def test_all_types_subscriber_receives_every_type():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(object_types=None, subscriber="user", handler=sink)

    bus.publish(_event(_obj(ObjectType.INTENT, id="int_1", owner="user")))
    bus.publish(_event(_obj(ObjectType.MEMORY, id="mem_1", owner="user")))

    assert {e.object_type for e in sink.received} == {ObjectType.INTENT, ObjectType.MEMORY}
    assert len(sink.received) == 2


def test_multi_type_subscription_matches_any_in_set():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(object_types={ObjectType.PLAN, ObjectType.ACTION}, subscriber="user", handler=sink)

    bus.publish(_event(_obj(ObjectType.PLAN, owner="user")))
    bus.publish(_event(_obj(ObjectType.ACTION, owner="user")))
    bus.publish(_event(_obj(ObjectType.JUDGMENT, owner="user")))

    assert {e.object_type for e in sink.received} == {ObjectType.PLAN, ObjectType.ACTION}


# --------------------------------------------------------------------------- #
# (b) Permission gating (envelope-based, fail-closed)
# --------------------------------------------------------------------------- #
def test_owner_receives_private_object():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(subscriber="alice", handler=sink)

    bus.publish(_event(_obj(ObjectType.MEMORY, owner="alice", visibility="private")))
    assert len(sink.received) == 1


def test_allowed_agent_receives_shared_object():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(subscriber="agent_x", handler=sink)

    obj = _obj(ObjectType.JUDGMENT, owner="alice", visibility="shared", allowed_agents=["agent_x"])
    assert bus.publish(_event(obj)) == 1
    assert len(sink.received) == 1


def test_non_listed_private_subscriber_is_denied():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(subscriber="mallory", handler=sink)

    # Private, owned by alice, mallory not on the allow-list → DENY.
    obj = _obj(ObjectType.MEMORY, owner="alice", visibility="private", allowed_agents=["agent_x"])
    assert bus.publish(_event(obj)) == 0
    assert sink.received == []


def test_shared_is_not_a_broadcast_to_unlisted_agents():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(subscriber="stranger", handler=sink)

    # "shared" reaches only owner + allow-list, never an unlisted subscriber.
    obj = _obj(ObjectType.PLAN, owner="alice", visibility="shared", allowed_agents=["agent_x"])
    assert bus.publish(_event(obj)) == 0


def test_public_object_broadcasts_to_all_including_anonymous():
    bus = CognitiveBus()
    named = _Collector()
    anon = _Collector()
    bus.subscribe(subscriber="whoever", handler=named)
    bus.subscribe(subscriber=None, handler=anon)  # anonymous

    obj = _obj(ObjectType.REFLECTION, owner="alice", visibility="public")
    assert bus.publish(_event(obj)) == 2
    assert len(named.received) == 1
    assert len(anon.received) == 1


def test_anonymous_subscriber_denied_private_object_fail_closed():
    bus = CognitiveBus()
    anon = _Collector()
    bus.subscribe(subscriber=None, handler=anon)

    bus.publish(_event(_obj(ObjectType.MEMORY, owner="alice", visibility="private")))
    bus.publish(_event(_obj(ObjectType.MEMORY, owner="alice", visibility="shared")))
    assert anon.received == []  # anonymous gets nothing non-public (fail-closed)


# --------------------------------------------------------------------------- #
# (c) Predicate filtering
# --------------------------------------------------------------------------- #
def test_predicate_filters_within_matched_type_and_permission():
    bus = CognitiveBus()
    sink = _Collector()
    bus.subscribe(
        object_types={ObjectType.ACTION},
        subscriber="user",
        predicate=lambda e: e.verb == "ACT",
        handler=sink,
    )

    bus.publish(_event(_obj(ObjectType.ACTION, id="act_1", owner="user"), verb="ACT"))
    bus.publish(_event(_obj(ObjectType.ACTION, id="act_2", owner="user"), verb="PLAN"))

    assert [e.object_id for e in sink.received] == ["act_1"]


def test_raising_predicate_is_isolated_and_treated_as_no_match():
    bus = CognitiveBus()
    sink = _Collector()

    def boom(_e: CognitiveEvent) -> bool:
        raise RuntimeError("bad predicate")

    bus.subscribe(subscriber="user", predicate=boom, handler=sink)
    # A raising predicate must not break the bus and yields no delivery.
    assert bus.publish(_event(_obj(ObjectType.MEMORY, owner="user"))) == 0
    assert sink.received == []


def test_raising_handler_does_not_stop_fanout():
    bus = CognitiveBus()
    good = _Collector()

    def boom(_e: CognitiveEvent) -> None:
        raise RuntimeError("bad handler")

    bus.subscribe(subscriber="user", handler=boom)
    bus.subscribe(subscriber="user", handler=good)

    # The good subscriber still gets it; the bad handler is counted as not-delivered.
    delivered = bus.publish(_event(_obj(ObjectType.MEMORY, owner="user")))
    assert delivered == 1
    assert len(good.received) == 1


# --------------------------------------------------------------------------- #
# (d) CognitiveEvent serialization round-trip
# --------------------------------------------------------------------------- #
def test_cognitive_event_to_from_dict_round_trip():
    obj = _obj(
        ObjectType.INTENT,
        id="int_42",
        owner="alice",
        visibility="shared",
        allowed_agents=["agent_x", "agent_y"],
    )
    event = CognitiveEvent(
        object_ref=obj,
        verb=CanonicalVerb.FORM_INTENT.value,
        actor="intent_agent",
        payload={"reason": "directional"},
    )

    restored = CognitiveEvent.from_dict(event.to_dict())

    assert restored.verb == "FORM_INTENT"
    assert restored.actor == "intent_agent"
    assert restored.payload == {"reason": "directional"}
    assert restored.timestamp == event.timestamp
    assert restored.object_id == "int_42"
    assert restored.object_type == ObjectType.INTENT
    assert restored.object_ref.permissions.allowed_agents == ["agent_x", "agent_y"]
    assert restored.to_dict() == event.to_dict()


def test_accessors_delegate_to_envelope():
    obj = _obj(ObjectType.PLAN, id="plan_9", owner="user")
    event = _event(obj)
    assert event.object_type is ObjectType.PLAN
    assert event.object_id == "plan_9"


# --------------------------------------------------------------------------- #
# (e) envelope_allows directly
# --------------------------------------------------------------------------- #
def test_envelope_allows_owner():
    obj = _obj(ObjectType.MEMORY, owner="alice", visibility="private")
    assert envelope_allows(obj, "alice") is True


def test_envelope_allows_listed_agent():
    obj = _obj(ObjectType.MEMORY, owner="alice", visibility="private", allowed_agents=["bob"])
    assert envelope_allows(obj, "bob") is True


def test_envelope_allows_denies_unlisted_private():
    obj = _obj(ObjectType.MEMORY, owner="alice", visibility="private", allowed_agents=["bob"])
    assert envelope_allows(obj, "carol") is False


def test_envelope_allows_public_for_anyone_and_anonymous():
    obj = _obj(ObjectType.MEMORY, owner="alice", visibility="public")
    assert envelope_allows(obj, "carol") is True
    assert envelope_allows(obj, None) is True


def test_envelope_allows_anonymous_denied_non_public():
    private = _obj(ObjectType.MEMORY, owner="alice", visibility="private")
    shared = _obj(ObjectType.MEMORY, owner="alice", visibility="shared", allowed_agents=["bob"])
    assert envelope_allows(private, None) is False
    assert envelope_allows(shared, None) is False


# --------------------------------------------------------------------------- #
# unsubscribe + null bus
# --------------------------------------------------------------------------- #
def test_unsubscribe_stops_delivery():
    bus = CognitiveBus()
    sink = _Collector()
    sub = bus.subscribe(subscriber="user", handler=sink)

    bus.publish(_event(_obj(ObjectType.MEMORY, owner="user")))
    bus.unsubscribe(sub)
    bus.publish(_event(_obj(ObjectType.MEMORY, owner="user")))

    assert len(sink.received) == 1


def test_null_cognitive_bus_drops_everything():
    sink = _Collector()
    NULL_COGNITIVE_BUS.subscribe(subscriber="user", handler=sink)
    assert NULL_COGNITIVE_BUS.publish(_event(_obj(ObjectType.MEMORY, owner="user"))) == 0
    assert sink.received == []
    assert isinstance(NULL_COGNITIVE_BUS, NullCognitiveBus)
