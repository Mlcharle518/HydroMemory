"""Agent base contracts (PRD §8).

The eight §8 roles are implemented as *synchronous library objects*, not daemons
or event-loop subscribers. Each agent holds an injected ``engine`` (a duck-typed
"verbs" object that owns storage + the lifecycle/recall/governance operations)
and exposes a single :meth:`Agent.run` method invoked by the
:class:`~hydromemory.agents.registry.AgentRuntime`.

The ``engine`` is deliberately duck-typed: each role documents and calls only the
narrow slice of methods it needs (e.g. the Filtration agent calls
``engine.assess_and_route`` and ``engine.filter``). Tests inject a mock engine
and assert those calls. The real engine (Track B) implements the union of these
methods; this package never imports it directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from hydromemory.governance import AgentIdentity, TrustLevel


@dataclass
class AgentContext:
    """Per-tick context threaded through :meth:`Agent.run`.

    ``stage`` names the lifecycle stage the runtime is ticking (e.g. ``capture``,
    ``maintain``, ``recall``); roles may ignore stages that are not theirs.
    ``payload`` carries stage inputs (raw events, a recall query, candidate
    droplets). ``results`` accumulates each agent's output keyed by agent name so
    later agents in the same tick can read earlier results. ``data`` is free
    scratch space.
    """

    stage: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def record(self, agent_name: str, value: Any) -> None:
        self.results[agent_name] = value


@runtime_checkable
class Agent(Protocol):
    """Structural protocol every §8 role satisfies."""

    name: str
    trust_level: TrustLevel

    def run(self, ctx: AgentContext) -> Any:
        """Perform this agent's work for the given context, returning its output."""
        ...


class BaseAgent:
    """Concrete convenience base: holds the engine, name, trust, and identity.

    Subclasses set :attr:`name` / :attr:`trust_level` (and optionally the
    filtration / user-proxy flags) and override :meth:`run`.
    """

    name: str = "agent"
    trust_level: TrustLevel = TrustLevel.SESSION
    is_filtration: bool = False
    is_user_proxy: bool = False

    #: Stages this agent acts on. Empty tuple means "every stage".
    stages: tuple[str, ...] = ()

    def __init__(self, engine: Any, *, name: str | None = None) -> None:
        self.engine = engine
        if name is not None:
            self.name = name

    def identity(self) -> AgentIdentity:
        """The governance :class:`AgentIdentity` this agent acts under."""
        return AgentIdentity(
            name=self.name,
            trust_level=self.trust_level,
            is_filtration=self.is_filtration,
            is_user_proxy=self.is_user_proxy,
        )

    def handles(self, stage: str) -> bool:
        """Whether this agent should run for ``stage`` (empty stages = always)."""
        return not self.stages or stage in self.stages

    def run(self, ctx: AgentContext) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError
