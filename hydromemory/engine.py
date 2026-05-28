"""Assembled engine facade (Phase 2 integration).

Wires the independently-built layers — storage (Track A), intelligence (Track A),
the lifecycle engine + verbs + HQL + pipeline (Track B), and governance +
forgetting + contamination (Track C) — into a single :class:`Engine` object used
by the CLI, the §12 example scripts, and the (Phase 4) HTTP server.

This is the one place the real concrete implementations are injected into each
other; everything underneath stays dependency-injected and individually testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hydromemory import contamination as contamination_mod
from hydromemory import forgetting as forgetting_mod
from hydromemory.bus.bus import EventBus
from hydromemory.bus.emit import NULL_EMITTER, Emitter
from hydromemory.config import HydroConfig
from hydromemory.governance import (
    AccessContext,
    AgentIdentity,
    TrustLevel,
    check_access,
    permission_score,
    privacy_risk,
)
from hydromemory.hql import execute as hql_execute
from hydromemory.hql import parse as hql_parse
from hydromemory.intelligence import Intelligence, build_intelligence
from hydromemory.pipeline import process_experience, recall_for_agent
from hydromemory.reader import ReaderResult, build_composer, compose_answer
from hydromemory.recall import RecallWeights
from hydromemory.storage import DropletRepository, open_store
from hydromemory.verbs import Verbs


@dataclass
class Engine:
    """A fully-wired HydroMemory instance over one SQLite store."""

    config: HydroConfig
    repo: DropletRepository
    intelligence: Intelligence
    verbs: Verbs
    emit: Emitter = NULL_EMITTER
    # HydroIntent surface (ADR-0037), present only when config.intents_enabled.
    # Typed as Any to keep the memory engine free of a hard import on the layer.
    intents: Any = None
    # HydroJudgment surface (ADR-0043), present only when config.judgment_enabled.
    judgment: Any = None
    # HydroPlan surface (ADR-0044), present only when config.planning_enabled.
    plan: Any = None
    # HydroAction surface (ADR-0045), present only when config.action_enabled.
    action: Any = None
    # HydroReflect surface (ADR-0046), present only when config.reflect_enabled.
    reflect: Any = None
    # HydroSense surface (ADR-0051), present only when config.sense_enabled (stack position 1).
    sense: Any = None
    # HydroIdentity surface (ADR-0052), present only when config.identity_enabled (position 3).
    identity: Any = None
    # HydroIntegrate surface (ADR-0050), present only when config.integrate_enabled.
    integrate: Any = None
    # Unified HydroCognitive event bus (ADR-0049), used by HydroIntegrate's NOTIFY_AGENTS.
    cognitive_bus: Any = None

    # -- high-level operations ------------------------------------------------
    def absorb(
        self,
        content: str,
        *,
        source: str = "conversation",
        context: dict[str, Any] | None = None,
        agent: AgentIdentity | None = None,
    ) -> dict[str, Any]:
        """Run the full §14 capture pipeline (classify -> phase -> reservoir ->
        triggers -> governance review -> store) and return the decision dict."""
        event = {"content": content, "source": source}
        return process_experience(
            event,
            context or {},
            repo=self.repo,
            intelligence=self.intelligence,
            check_access=check_access,
            agent=agent,
            emit=self.emit,
        )

    def recall(
        self,
        query: str,
        *,
        agent: AgentIdentity | None = None,
        context: AccessContext | dict[str, Any] | None = None,
    ) -> list[Any]:
        """Run the §14 recall pipeline; return ranked RecallResult objects."""
        agent = agent or AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED)
        return recall_for_agent(
            query,
            agent,
            context or {},
            repo=self.repo,
            intelligence=self.intelligence,
            permission_score=permission_score,
            privacy_risk=privacy_risk,
            emit=self.emit,
        )

    def hql(
        self,
        query_text: str,
        *,
        agent: AgentIdentity | None = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Parse + execute an HQL statement (GET/PRECIPITATE/FILTER/DISTILL)."""
        query = hql_parse(query_text)

        def _recall(op: dict[str, Any]) -> list[Any]:
            inner = op.get("query", {})
            query_str = inner.get("theme") or inner.get("trigger") or ""
            merged_ctx: dict[str, Any] = dict(context or {})
            merged_ctx.update(inner)
            return self.recall(query_str, agent=agent, context=merged_ctx)

        return hql_execute(query, self.repo, recall=_recall, verbs=self.verbs)

    def answer(
        self,
        query: str,
        *,
        agent: AgentIdentity | None = None,
        traverse: bool = True,
        weights: RecallWeights | None = None,
        composer: Any = None,
        k: int = 10,
    ) -> ReaderResult:
        """Recall the constellation, then compose an answer with citations (ADR-0035).

        Recall runs through ``precipitate`` with spreading-activation traversal on by
        default (``traverse=True`` + a default ``activation_bonus`` so the constellation
        actually contributes), then a reader composes the answer over the recalled
        droplets. ``composer`` defaults to the offline extractive composer unless the
        Claude backend is configured (then an LLM composer). Returns a
        :class:`~hydromemory.reader.ReaderResult` (answer + cited droplet ids).
        """
        agent = agent or AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED)
        if weights is None and traverse:
            weights = RecallWeights(activation_bonus=1.0)
        response = self.verbs.precipitate(query, agent=agent, k=k, traverse=traverse, weights=weights)
        droplets = []
        for result in response.result:
            droplet = self.repo.get(result.droplet_id)
            if droplet is not None:
                droplets.append(droplet)
        return compose_answer(query, droplets, composer=composer or build_composer(self.config))

    def attach_bus(self, bus: EventBus, *, actor: str = "engine", app_id: str | None = None) -> Emitter:
        """Route this engine's lifecycle events (verbs + pipeline) to ``bus``.

        If ``bus`` has no repo of its own, this engine's repo is bound into it so
        its permission gate can resolve droplets (the gate is fail-closed and
        would otherwise deny every droplet-bearing event to an identified
        subscriber — see :class:`~hydromemory.bus.bus.EventBus`).
        """
        _ensure_bus_repo(bus, self.repo)
        self.emit = Emitter(bus, actor=actor, app_id=app_id)
        self.verbs.emit = self.emit
        return self.emit

    def close(self) -> None:
        if self.intents is not None:
            self.intents.intent_repo.close()
        if self.judgment is not None:
            self.judgment.judgment_repo.close()
        if self.plan is not None:
            self.plan.plan_repo.close()
        if self.action is not None:
            self.action.action_repo.close()
        if self.reflect is not None:
            self.reflect.reflection_repo.close()
        if self.sense is not None:
            self.sense.observation_repo.close()
        if self.identity is not None:
            self.identity.identity_repo.close()
        if self.integrate is not None:
            self.integrate.reintegration_repo.close()
        self.repo.close()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _ensure_bus_repo(bus: EventBus, repo: DropletRepository) -> None:
    """Bind ``repo`` into ``bus`` for permission gating if it has none.

    The bus permission gate is fail-closed: a repo-less bus cannot load the
    event's droplet and therefore denies delivery of any droplet-bearing event to
    an identified subscriber. Wiring the engine's repo restores legitimate,
    access-checked delivery. A bus that already carries a repo is left untouched
    (e.g. the server builds ``EventBus(repo=engine.repo)`` explicitly).
    """
    if getattr(bus, "_repo", None) is None:
        bus._repo = repo


def build_engine(config: HydroConfig | None = None, *, bus: EventBus | None = None) -> Engine:
    """Construct a fully-wired :class:`Engine` from configuration (env by default).

    If ``bus`` is given, the engine's verbs + pipeline emit lifecycle events to it;
    otherwise emission is a no-op (v1 behavior).
    """
    config = config or HydroConfig.from_env()
    repo = open_store(config)
    intelligence = build_intelligence(config)
    if bus is not None:
        # Bind the engine's repo so the (fail-closed) permission gate can resolve
        # droplets; a repo-less bus would deny every droplet-bearing event to an
        # identified subscriber.
        _ensure_bus_repo(bus, repo)
        emit = Emitter(bus)
    else:
        emit = NULL_EMITTER
    verbs = Verbs(
        repo=repo,
        intelligence=intelligence,
        check_access=check_access,
        forgetting=forgetting_mod,
        contamination=contamination_mod,
        permission_score=permission_score,
        privacy_risk=privacy_risk,
        emit=emit,
    )
    intents = None
    if config.intents_enabled:
        # Lazy import: the HydroIntent layer is only loaded when explicitly enabled,
        # so the default memory-only engine carries no extra import cost (ADR-0025).
        from hydromemory.hydrointent.engine import build_intent_verbs

        intents = build_intent_verbs(
            config, droplet_repo=repo, intelligence=intelligence, emit=emit
        )
    judgment = None
    if config.judgment_enabled:
        from hydromemory.hydrojudgment.engine import build_judgment_verbs

        judgment = build_judgment_verbs(config)
    plan = None
    if config.planning_enabled:
        from hydromemory.hydroplan.engine import build_plan_verbs

        plan = build_plan_verbs(config)
    action = None
    if config.action_enabled:
        from hydromemory.hydroaction.engine import build_action_verbs

        action = build_action_verbs(config)
    reflect = None
    if config.reflect_enabled:
        from hydromemory.hydroreflect.engine import build_reflect_verbs

        reflect = build_reflect_verbs(config)
    sense = None
    if config.sense_enabled:
        from hydromemory.hydrosense.engine import build_sense_verbs

        def _absorb_observation(event: Any) -> str:
            # HydroSense routes a salient observation to HydroMemory's ABSORB — Memory decides
            # what persists (Master Spec §6); Sense never writes durable meaning itself.
            droplet = verbs.absorb(event.content, source=f"sense:{event.source}")
            return droplet.id

        sense = build_sense_verbs(config, memory_absorber=_absorb_observation)
    identity = None
    if config.identity_enabled:
        from hydromemory.hydroidentity.engine import build_identity_verbs

        identity = build_identity_verbs(
            config, droplet_repo=repo, intelligence=intelligence, emit=emit
        )
    # §16 Intent↔Identity seam: when both are enabled, intent formation aligns to the identity
    # posture (the active value/boundary anchors). Default-off keeps Intent memory-only.
    if intents is not None and identity is not None:
        intents.identity = identity
    integrate = None
    cognitive_bus = None
    if config.integrate_enabled:
        from hydromemory.cognitive_bus import CognitiveBus
        from hydromemory.hydrointegrate.engine import build_integrate_verbs

        cognitive_bus = CognitiveBus()
        # Pass whatever layer surfaces are enabled so Integrate can commit each update_type to its
        # target (identity_update->HydroIdentity, intent_update->HydroIntent, rules/patterns/policies
        # ->CLOUD principles) instead of recording-only — closing the loop back into the stack.
        integrate = build_integrate_verbs(
            config,
            memory_verbs=verbs,
            droplet_repo=repo,
            identity_verbs=identity,
            intent_verbs=intents,
            bus=cognitive_bus,
        )
    return Engine(
        config=config,
        repo=repo,
        intelligence=intelligence,
        verbs=verbs,
        emit=emit,
        intents=intents,
        judgment=judgment,
        plan=plan,
        action=action,
        reflect=reflect,
        sense=sense,
        identity=identity,
        integrate=integrate,
        cognitive_bus=cognitive_bus,
    )
