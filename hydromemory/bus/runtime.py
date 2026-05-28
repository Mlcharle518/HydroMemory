"""Event-driven agent runtime (PRD §9, v2 Phase B1).

:class:`BusAgentRuntime` is the §9 counterpart to the synchronous
:class:`~hydromemory.agents.registry.AgentRuntime`. Instead of an ordered
in-process ``tick(stage)`` loop, each §8 agent is *subscribed* to the bus on the
event topics that correspond to the lifecycle stages it ``handles``. When a
matching :class:`~hydromemory.bus.events.MemoryEvent` is published, the agent is
invoked with an :class:`~hydromemory.agents.base.AgentContext` carrying the event
on ``payload['event']``; its result is recorded (error-isolated).

This module is purely additive: it does **not** modify ``AgentRuntime.tick`` (the
synchronous seam) — it provides a parallel, bus-driven runtime that reuses the
same agents. ``bus_runtime_from_engine`` mirrors
:func:`~hydromemory.agents.registry.build_default_runtime`.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import Agent, AgentContext
from hydromemory.bus.bus import EventBus, Subscription
from hydromemory.bus.events import EventType, MemoryEvent

#: Map a lifecycle *stage* (what an agent ``handles``) to the bus *topics* that
#: stage reacts to. An agent with no declared stages subscribes to all topics.
STAGE_TOPICS: dict[str, tuple[EventType, ...]] = {
    "capture": (EventType.ABSORBED,),
    "maintain": (
        EventType.FLOWED,
        EventType.EVAPORATED,
        EventType.CONDENSED,
        EventType.INFILTRATED,
        EventType.TRANSFORMED,
        EventType.IRRIGATED,
        EventType.MELTED,
    ),
    "recall": (EventType.RECALLED,),
    "expose": (EventType.RECALLED,),
    "filter": (EventType.FILTERED, EventType.POLLUTED),
    "reflect": (EventType.TRANSFORMED, EventType.CONDENSED),
    "distill": (EventType.DISTILLED, EventType.CONDENSED),
    "archive": (
        EventType.ARCHIVED,
        EventType.FROZEN,
        EventType.DRAINED,
        EventType.FORGOTTEN,
    ),
}

#: Reverse index (topic value -> the stage it should drive ``run`` under). When a
#: topic maps to several stages, the first stage registered for it wins; this only
#: feeds the ``ctx.stage`` label, not delivery (delivery is by topic membership).
_TOPIC_STAGE: dict[str, str] = {}
for _stage, _topics in STAGE_TOPICS.items():
    for _t in _topics:
        _TOPIC_STAGE.setdefault(_t.value, _stage)


def topics_for_stages(stages: tuple[str, ...]) -> frozenset[str] | None:
    """Translate an agent's ``stages`` to a bus topic set.

    Empty ``stages`` -> ``None`` (subscribe to every topic). Unknown stages
    contribute no topics (so an agent on only unknown stages gets an empty set
    and never fires — intentional, rather than silently receiving everything).
    """
    if not stages:
        return None
    topics: set[str] = set()
    for stage in stages:
        for t in STAGE_TOPICS.get(stage, ()):  # unknown stage -> no topics
            topics.add(t.value)
    return frozenset(topics)


def _stage_for_event(event: MemoryEvent, agent: Agent) -> str:
    """Pick the ``ctx.stage`` label for an event delivered to ``agent``.

    Prefer a stage the agent actually handles (so role logic keyed on ``stage``
    behaves), else the canonical stage for the topic, else the raw event type.
    """
    agent_stages: tuple[str, ...] = tuple(getattr(agent, "stages", ()) or ())
    for stage in agent_stages:
        if event.type in {t.value for t in STAGE_TOPICS.get(stage, ())}:
            return stage
    return _TOPIC_STAGE.get(event.type, event.type)


class BusAgentRuntime:
    """Subscribes §8 agents to the bus and drives them on matching events.

    Each registered agent becomes one sync subscription on the topics derived
    from its ``stages``. On a delivered event the runtime builds an
    :class:`AgentContext` (``stage`` derived, ``payload={"event": event}``),
    calls ``agent.run(ctx)`` guarded against exceptions, and records the result
    (the most recent result per agent) on :attr:`last_results`.
    """

    def __init__(self, bus: EventBus, agents: list[Agent] | None = None) -> None:
        self.bus = bus
        self._agents: list[Agent] = []
        self._subs: list[Subscription] = []
        #: Most-recent ``run`` result per agent name (populated on each delivery).
        self.last_results: dict[str, Any] = {}
        #: Per-agent delivery count (handy for tests / observability).
        self.handled: dict[str, int] = {}
        for agent in agents or []:
            self.register(agent)

    @property
    def agents(self) -> tuple[Agent, ...]:
        return tuple(self._agents)

    def register(self, agent: Agent) -> Agent:
        """Register ``agent`` and subscribe it to the topics for its stages."""
        self._agents.append(agent)
        self.handled.setdefault(agent.name, 0)
        topics = topics_for_stages(tuple(getattr(agent, "stages", ()) or ()))
        subscriber = agent.identity() if hasattr(agent, "identity") else None
        sub = self.bus.subscribe(
            topics=topics,
            handler=self._make_handler(agent),
            subscriber=subscriber,
        )
        self._subs.append(sub)
        return agent

    def close(self) -> None:
        """Unsubscribe every agent (e.g. on shutdown / test teardown)."""
        for sub in self._subs:
            self.bus.unsubscribe(sub)
        self._subs.clear()

    def _make_handler(self, agent: Agent) -> Any:
        def _handle(event: MemoryEvent) -> None:
            ctx = AgentContext(stage=_stage_for_event(event, agent), payload={"event": event})
            try:
                result = agent.run(ctx)
            except Exception as exc:  # noqa: BLE001 - isolate a failing agent.
                self.last_results[agent.name] = {"error": repr(exc)}
                return
            self.last_results[agent.name] = result
            self.handled[agent.name] = self.handled.get(agent.name, 0) + 1

        return _handle


def bus_runtime_from_engine(engine: Any, bus: EventBus) -> BusAgentRuntime:
    """Construct a :class:`BusAgentRuntime` with all eight §8 roles subscribed.

    Mirrors :func:`~hydromemory.agents.registry.build_default_runtime`: the same
    roles in the same order, all sharing the injected ``engine``, but wired as bus
    subscribers rather than an ordered ``tick`` loop.
    """
    # Imported here to avoid a circular import at module load (mirrors the
    # registry's lazy imports).
    from hydromemory.agents.archivist import ArchivistAgent
    from hydromemory.agents.capture import CaptureAgent
    from hydromemory.agents.distillation import DistillationAgent
    from hydromemory.agents.filtration import FiltrationAgent
    from hydromemory.agents.hydrologist import HydrologistAgent
    from hydromemory.agents.privacy import PrivacyAgent
    from hydromemory.agents.recall_agent import RecallAgent
    from hydromemory.agents.reflection import ReflectionAgent

    runtime = BusAgentRuntime(bus)
    runtime.register(CaptureAgent(engine))
    runtime.register(HydrologistAgent(engine))
    runtime.register(FiltrationAgent(engine))
    runtime.register(PrivacyAgent(engine))
    runtime.register(RecallAgent(engine))
    runtime.register(ReflectionAgent(engine))
    runtime.register(DistillationAgent(engine))
    runtime.register(ArchivistAgent(engine))
    return runtime
