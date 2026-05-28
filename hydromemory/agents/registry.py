"""Agent runtime / registry (PRD §8, with §9 deferred).

:class:`AgentRuntime` is the synchronous seam where the §8 roles are registered
and invoked in order for a given lifecycle stage. ``tick(stage, ctx)`` runs every
registered agent that :meth:`~hydromemory.agents.base.BaseAgent.handles` the
stage, in registration order, recording each agent's output into
``ctx.results``.

§9 (OS / platform integration) is intentionally *deferred*. At the OS level
HydroMemory becomes a publish/subscribe **memory event bus** with four
integration levels — L1 App Memory, L2 User Memory Vault, L3 Agentic Memory
Mesh, L4 Sovereign Cognitive OS. This synchronous ``tick`` loop is the exact
point where that event bus would later slot in: instead of calling agents
in-process and in-order, a future runtime would publish stage events and let
agents subscribe (with the same per-agent permission checks). Until then, the
ordered in-process call is a faithful, testable stand-in.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents.base import Agent, AgentContext


class AgentRuntime:
    """Registers §8 agents and ticks them synchronously per lifecycle stage."""

    def __init__(self) -> None:
        self._agents: list[Agent] = []

    def register(self, agent: Agent) -> Agent:
        """Register ``agent``; returns it for fluent use. Order is preserved."""
        self._agents.append(agent)
        return agent

    @property
    def agents(self) -> tuple[Agent, ...]:
        return tuple(self._agents)

    def tick(self, stage: str, ctx: AgentContext | None = None) -> AgentContext:
        """Run every agent that handles ``stage``, in registration order.

        Builds (or reuses) an :class:`AgentContext` with ``stage`` set, invokes
        each applicable agent's ``run``, records its result under the agent name,
        and returns the populated context. This is the deferred-§9 event-bus
        seam (see module docstring).
        """
        if ctx is None:
            ctx = AgentContext(stage=stage)
        else:
            ctx.stage = stage

        for agent in self._agents:
            handles = getattr(agent, "handles", None)
            if callable(handles) and not handles(stage):
                continue
            result = agent.run(ctx)
            ctx.record(agent.name, result)
        return ctx


def build_default_runtime(engine: Any) -> AgentRuntime:
    """Construct an :class:`AgentRuntime` with all eight §8 roles registered.

    Registration order follows the §8 table and the natural lifecycle flow:
    capture -> hydrologist -> filtration -> privacy -> recall -> reflection ->
    distillation -> archivist. All roles share the single injected ``engine``.
    """
    # Imported here to avoid a circular import at module load (roles import base,
    # base imports governance; the runtime only needs them at construction time).
    from hydromemory.agents.archivist import ArchivistAgent
    from hydromemory.agents.capture import CaptureAgent
    from hydromemory.agents.distillation import DistillationAgent
    from hydromemory.agents.filtration import FiltrationAgent
    from hydromemory.agents.hydrologist import HydrologistAgent
    from hydromemory.agents.privacy import PrivacyAgent
    from hydromemory.agents.recall_agent import RecallAgent
    from hydromemory.agents.reflection import ReflectionAgent

    runtime = AgentRuntime()
    runtime.register(CaptureAgent(engine))
    runtime.register(HydrologistAgent(engine))
    runtime.register(FiltrationAgent(engine))
    runtime.register(PrivacyAgent(engine))
    runtime.register(RecallAgent(engine))
    runtime.register(ReflectionAgent(engine))
    runtime.register(DistillationAgent(engine))
    runtime.register(ArchivistAgent(engine))
    return runtime
