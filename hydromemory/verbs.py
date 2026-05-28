"""The 15 HydroMemory API verbs (PRD §5.7, §6).

:class:`Verbs` bundles the engine's public operations. Every external dependency
is *injected* at construction so the verbs can be unit-tested with mocks:

* ``repo``           -- :class:`~hydromemory.storage.repository.DropletRepository`
* ``intelligence``  -- :class:`~hydromemory.intelligence.base.Intelligence`
* ``check_access``  -- the governance ``check_access`` entry point
* ``forgetting``    -- module with ``drain/sediment/seal/delete`` (Track C)
* ``contamination`` -- module with ``mark_polluted/filter_droplet`` (Track C)
* ``permission_score`` / ``privacy_risk`` -- governance scorers (Track C)
* ``phase_config``  -- :class:`~hydromemory.phases.PhaseConfig`

Co-owned verbs (FREEZE/FILTER/POLLUTE/DRAIN/ARCHIVE/FORGET) **delegate** to the
governance/forgetting/contamination modules rather than re-implementing policy.

Each verb returns either a :class:`~hydromemory.schema.Droplet` or a
:class:`~hydromemory.protocol.ProtocolResponse` (the recall/query-shaped verbs),
whichever is the natural unit for that operation.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hydromemory.activation import (
    DEFAULT_ACTIVATION_PARAMS,
    LINK_KINDS,
    ActivationParams,
    spread_activation,
)
from hydromemory.bus.emit import NULL_EMITTER, Emitter
from hydromemory.bus.events import EventType
from hydromemory.intelligence import Intelligence
from hydromemory.phases import DEFAULT_PHASE_CONFIG, PhaseConfig, apply_phase_transition
from hydromemory.protocol import ProtocolEnvelope, ProtocolResponse
from hydromemory.recall import (
    RecallMode,
    RecallWeights,
    format_recall,
    hydro_recall_score,
    recall_threshold,
    select_recall_mode,
)
from hydromemory.reservoirs import Reservoir, normalize_reservoir
from hydromemory.schema import (
    Droplet,
    Phase,
    State,
    clamp_unit,
    new_id,
)
from hydromemory.storage.repository import DropletRepository
from hydromemory.triggers import Trigger, detect_triggers

# Governance scorer signatures (injected; Track C provides the implementations).
PermissionScorer = Callable[..., float]
PrivacyScorer = Callable[..., float]


def _default_permission_score(droplet: Droplet, agent: Any) -> float:
    """Fallback permission score if governance scorer isn't injected.

    Returns 1.0 when the agent's name is in ``allowed_agents`` (or the list is
    empty / visibility is public), else 0.0. Real policy lives in Track C.
    """
    name = getattr(agent, "name", agent)
    allowed = droplet.permissions.allowed_agents
    if droplet.permissions.visibility.value == "public":
        return 1.0
    if not allowed or name in allowed:
        return 1.0
    return 0.0


def _default_privacy_risk(droplet: Droplet, context: Any = None) -> float:
    """Fallback privacy risk: private + sensitive -> high; else low."""
    risk = 0.0
    if droplet.permissions.visibility.value == "private":
        risk += 0.4
    sensitivity = droplet.meta.get("sensitivity")
    try:
        risk += 0.5 * float(sensitivity) if sensitivity is not None else 0.0
    except (TypeError, ValueError):
        pass
    if droplet.permissions.requires_consent_for_external_use:
        risk += 0.2
    return clamp_unit(risk)


@dataclass
class Verbs:
    repo: DropletRepository
    intelligence: Intelligence
    check_access: Callable[..., Any] | None = None
    forgetting: Any = None
    contamination: Any = None
    permission_score: PermissionScorer | None = None
    privacy_risk: PrivacyScorer | None = None
    phase_config: PhaseConfig = DEFAULT_PHASE_CONFIG
    # v2 §9: emit lifecycle events to the bus. Defaults to a no-op so v1 is unchanged.
    emit: Emitter = NULL_EMITTER

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _perm(self, droplet: Droplet, agent: Any) -> float:
        fn = self.permission_score or _default_permission_score
        return float(fn(droplet, agent))

    def _privacy(self, droplet: Droplet, context: Any = None) -> float:
        fn = self.privacy_risk or _default_privacy_risk
        return float(fn(droplet, context))

    # ------------------------------------------------------------------ #
    # 1. ABSORB -- create a memory droplet from experience.
    # ------------------------------------------------------------------ #
    def absorb(
        self,
        content: str,
        *,
        source: str = "experience",
        context: dict[str, Any] | None = None,
        reservoir: Reservoir | str = Reservoir.WORKING_STREAM,
        state: State | None = None,
        envelope: ProtocolEnvelope | None = None,
    ) -> Droplet:
        """Encode + classify + store a new LIQUID droplet.

        If a §6 ``envelope`` is supplied, its ``input``/``classification``/
        ``initial_state``/``permissions`` blocks seed the droplet.
        """
        ctx = dict(context or {})
        if envelope is not None:
            content = str(envelope.input.get("content", content))
            source = str(envelope.input.get("source", source))
            ctx = dict(envelope.input.get("context") or ctx)
            if envelope.initial_state.get("reservoir"):
                reservoir = envelope.initial_state["reservoir"]

        embedding = self.intelligence.embedder.embed(content)
        classification = self.intelligence.classifier.classify(content)

        st = state or State()
        droplet = Droplet(
            id=new_id(),
            content=content,
            source=source,
            phase=Phase.LIQUID,
            reservoir=normalize_reservoir(reservoir),
            memory_type=classification.memory_type,
            state=st,
            embedding=embedding,
        )
        if ctx:
            droplet.meta["context"] = ctx
        droplet.meta["importance"] = classification.importance
        droplet.meta["sensitivity"] = classification.sensitivity
        droplet.meta["expected_lifespan"] = classification.expected_lifespan

        self.repo.upsert(droplet)
        self.emit.emit(
            EventType.ABSORBED,
            droplet_id=droplet.id,
            payload={
                "phase": droplet.phase.value,
                "reservoir": droplet.reservoir.value,
                "memory_type": droplet.memory_type,
                "source": droplet.source,
            },
        )
        return droplet

    # ------------------------------------------------------------------ #
    # 2. FLOW -- connect memory to related memories (association links).
    # ------------------------------------------------------------------ #
    def flow(self, droplet: Droplet, related_ids: Sequence[str], *, kind: str = "associations") -> Droplet:
        """Add ``kind`` links from ``droplet`` to each related id (default associations)."""
        linked: list[str] = []
        for dst in related_ids:
            if dst == droplet.id:
                continue
            self.repo.add_link(droplet.id, kind, dst)
            target = getattr(droplet.links, kind, None)
            if isinstance(target, list) and dst not in target:
                target.append(dst)
            linked.append(dst)
        self.emit.emit(
            EventType.FLOWED,
            droplet_id=droplet.id,
            payload={"kind": kind, "related_ids": linked},
        )
        return droplet

    # ------------------------------------------------------------------ #
    # 3. EVAPORATE -- abstract a memory into a pattern (new VAPOR droplet).
    # ------------------------------------------------------------------ #
    def evaporate(self, droplet: Droplet) -> Droplet:
        """Abstract ``droplet`` into a new VAPOR droplet (derived_from link)."""
        essence = self.intelligence.abstractor.evaporate(droplet.content)
        vapor = Droplet(
            id=new_id(),
            content=essence,
            source=f"evaporate:{droplet.id}",
            phase=Phase.VAPOR,
            reservoir=Reservoir.CLOUD,
            memory_type=droplet.memory_type,
            state=State(
                temperature=clamp_unit(droplet.state.temperature + 0.2),
                fluidity=clamp_unit(droplet.state.fluidity + 0.1),
                purity=droplet.state.purity,
                confidence=droplet.state.confidence,
            ),
            embedding=self.intelligence.embedder.embed(essence),
        )
        vapor.meta["pattern"] = essence
        vapor.links.derived_from.append(droplet.id)
        self.repo.upsert(vapor)
        self.repo.add_link(vapor.id, "derived_from", droplet.id)
        self.emit.emit(
            EventType.EVAPORATED,
            droplet_id=vapor.id,
            payload={"source_id": droplet.id, "phase": vapor.phase.value},
        )
        self.emit.emit(
            EventType.TRANSFORMED,
            droplet_id=vapor.id,
            payload={"from_phase": droplet.phase.value, "to_phase": vapor.phase.value, "via": "evaporate"},
        )
        return vapor

    # ------------------------------------------------------------------ #
    # 4. CONDENSE -- cluster related abstracted memories into a CLOUD.
    # ------------------------------------------------------------------ #
    def condense(self, vapors: Sequence[Droplet], *, theme: str | None = None) -> Droplet:
        """Cluster VAPOR droplets into a single CLOUD droplet (derived_from each)."""
        if not vapors:
            raise ValueError("CONDENSE requires at least one vapor droplet")
        members = list(vapors)
        joined = theme or "; ".join(d.content for d in members)
        cloud = Droplet(
            id=new_id(),
            content=joined,
            source="condense",
            phase=Phase.CLOUD,
            reservoir=Reservoir.CLOUD,
            state=State(
                pressure=clamp_unit(sum(d.state.pressure for d in members) / len(members) + 0.1),
                confidence=clamp_unit(sum(d.state.confidence for d in members) / len(members)),
                purity=clamp_unit(sum(d.state.purity for d in members) / len(members)),
            ),
            embedding=self.intelligence.embedder.embed(joined),
        )
        cloud.meta["pattern"] = joined
        cloud.meta["members"] = [d.id for d in members]
        for d in members:
            cloud.links.derived_from.append(d.id)
        self.repo.upsert(cloud)
        for d in members:
            self.repo.add_link(cloud.id, "derived_from", d.id)
        self.emit.emit(
            EventType.CONDENSED,
            droplet_id=cloud.id,
            payload={"members": [d.id for d in members], "phase": cloud.phase.value},
        )
        return cloud

    # ------------------------------------------------------------------ #
    # 5. PRECIPITATE -- recall a memory into active use (the recall path).
    # ------------------------------------------------------------------ #
    def precipitate(
        self,
        query: str,
        *,
        agent: Any,
        query_ctx: dict[str, Any] | None = None,
        context: Any = None,
        k: int = 10,
        weights: RecallWeights | None = None,
        traverse: bool = False,
        activation_params: ActivationParams | None = None,
    ) -> ProtocolResponse:
        """Recall the best-matching droplets for ``query`` (scored + ranked).

        Returns a :class:`ProtocolResponse` whose ``result`` is a ranked list of
        rendered :class:`~hydromemory.recall.RecallResult` objects.

        With ``traverse=True`` the cosine seeds are expanded by query-conditioned
        spreading activation over the ``links`` graph (the §4 spine of
        docs/closing-the-gaps.md): a droplet the cosine top-k missed can surface
        because the question's activation reached it through its connections
        (multi-hop). The activation is added to each droplet's score weighted by
        ``RecallWeights.activation_bonus``. ``traverse=False`` (the default) is
        byte-identical to isolated v1 recall.
        """
        qctx = dict(query_ctx or {})
        embedding = self.intelligence.embedder.embed(query)
        agent_name = getattr(agent, "name", agent)

        def gate(d: Droplet) -> bool:
            return self._perm(d, agent) > 0.0

        hits = self.repo.search_similar(embedding, k=k, candidate_filter=gate)
        cosine_by_id: dict[str, float] = {did: cos for did, cos in hits}
        candidate_ids: list[str] = [did for did, _ in hits]
        activation: dict[str, float] = {}

        _cache: dict[str, Droplet | None] = {}

        def _get(did: str) -> Droplet | None:
            if did not in _cache:
                _cache[did] = self.repo.get(did)
            return _cache[did]

        if traverse:
            aparams = activation_params or DEFAULT_ACTIVATION_PARAMS

            def neighbors(did: str) -> list[tuple[str, str]]:
                d = _get(did)
                if d is None:
                    return []
                return [(dst, kind) for kind in LINK_KINDS for dst in getattr(d.links, kind)]

            def state_of(did: str) -> State | None:
                d = _get(did)
                return d.state if d is not None else None

            activation = spread_activation(
                {did: cos for did, cos in hits},
                neighbors,
                state_of,
                intent=qctx.get("intent"),
                params=aparams,
            )
            for rid in activation:
                if rid not in cosine_by_id and rid not in candidate_ids:
                    candidate_ids.append(rid)

        results: list[Any] = []
        for droplet_id in candidate_ids:
            d = _get(droplet_id)
            if d is None:
                continue
            perm = self._perm(d, agent)
            # Reached-by-traversal droplets must clear the same permission gate the
            # cosine seeds already passed inside search_similar (a no-op for seeds).
            if perm <= 0.0:
                continue
            privacy = self._privacy(d, context)
            contamination_penalty = 1.0 - d.state.purity
            score = hydro_recall_score(
                d,
                qctx,
                semantic_similarity=cosine_by_id.get(droplet_id, 0.0),
                permission_score=perm,
                privacy_risk=privacy,
                contamination_penalty=contamination_penalty,
                activation=activation.get(droplet_id, 0.0),
                weights=weights,
            )
            if score <= recall_threshold(d.phase, d.reservoir):
                continue
            mode = select_recall_mode(d, qctx)
            results.append(format_recall(d, mode, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        for r in results:
            self.emit.emit(
                EventType.RECALLED,
                droplet_id=getattr(r, "droplet_id", None),
                payload={"query": query, "agent": agent_name, "score": getattr(r, "score", None)},
            )
        return ProtocolResponse(
            operation="PRECIPITATE",
            result=results,
            outcome={"candidates": len(candidate_ids), "recalled": len(results), "agent": agent_name},
        )

    # ------------------------------------------------------------------ #
    # 6. INFILTRATE -- move a memory into deep storage (toward groundwater).
    # ------------------------------------------------------------------ #
    def infiltrate(self, droplet: Droplet, *, context: dict[str, Any] | None = None) -> Droplet:
        """Sink a memory toward GROUNDWATER (increase depth; settle the flow).

        Drives the §5.4 chain via REPETITION when the droplet is a RIVER;
        otherwise deepens the droplet directly (raising ``depth``/``gravity``).
        """
        ctx = dict(context or {})
        ctx.setdefault("cycle_count", max(droplet.cycle.cycle_count, self.phase_config.repetition_cycles))
        phase_before = droplet.phase
        if droplet.phase is Phase.RIVER:
            apply_phase_transition(droplet, Trigger.REPETITION, ctx, self.phase_config)
        else:
            droplet.state.depth = clamp_unit(droplet.state.depth + 0.3)
            droplet.state.gravity = clamp_unit(droplet.state.gravity + 0.1)
            if droplet.phase is Phase.LIQUID:
                droplet.phase = Phase.GROUNDWATER
            droplet.cycle.last_transformed = datetime.now(UTC)
        # Either branch sinks the droplet into the groundwater reservoir: the
        # direct-deepen path always does, and the RIVER path does once REPETITION
        # has settled it to GROUNDWATER. A single assignment covers both.
        droplet.reservoir = Reservoir.GROUNDWATER
        self.repo.upsert(droplet)
        self.emit.emit(
            EventType.INFILTRATED,
            droplet_id=droplet.id,
            payload={"phase": droplet.phase.value, "reservoir": droplet.reservoir.value},
        )
        if droplet.phase is not phase_before:
            self.emit.emit(
                EventType.TRANSFORMED,
                droplet_id=droplet.id,
                payload={"from_phase": phase_before.value, "to_phase": droplet.phase.value, "via": "infiltrate"},
            )
        return droplet

    # ------------------------------------------------------------------ #
    # 7. FREEZE -- preserve memory as a high-integrity ICE snapshot.
    #    (Co-owned: identity-write policy review is delegated to governance.)
    # ------------------------------------------------------------------ #
    def freeze(self, droplet: Droplet, *, agent: Any = None, context: Any = None) -> Droplet:
        """Freeze a memory into an ICE snapshot in the glacier.

        Delegates the identity-write policy review to governance ``check_access``
        (Operation.OVERWRITE). If access is denied, the droplet is returned
        unchanged (no snapshot is written).
        """
        if self.check_access is not None and agent is not None:
            from hydromemory.governance.obligations import Operation

            decision = self.check_access(droplet, agent, context, Operation.OVERWRITE)
            if not getattr(decision, "allowed", True):
                droplet.meta["freeze_denied"] = getattr(decision, "denial_reason", "policy review denied")
                return droplet

        droplet.phase = Phase.ICE
        droplet.reservoir = Reservoir.GLACIER
        droplet.state.integrity = clamp_unit(droplet.state.integrity + 0.2)
        droplet.state.temperature = clamp_unit(droplet.state.temperature - 0.4)
        droplet.state.fluidity = clamp_unit(droplet.state.fluidity - 0.5)
        droplet.cycle.last_transformed = datetime.now(UTC)
        self.repo.upsert(droplet)
        self.emit.emit(
            EventType.FROZEN,
            droplet_id=droplet.id,
            payload={"phase": droplet.phase.value, "reservoir": droplet.reservoir.value},
        )
        return droplet

    # ------------------------------------------------------------------ #
    # 8. MELT -- reactivate a preserved memory (ICE -> LIQUID) if safe.
    # ------------------------------------------------------------------ #
    def melt(self, droplet: Droplet, *, context: dict[str, Any] | None = None) -> Droplet:
        """Thaw an ICE snapshot back to LIQUID when the context is safe."""
        ctx = dict(context or {})
        if droplet.phase is not Phase.ICE:
            return droplet
        if not (ctx.get("safe_context") or ctx.get("safe")):
            droplet.meta["melt_blocked"] = "context not safe"
            return droplet
        apply_phase_transition(droplet, Trigger.SAFE_CONTEXT, ctx, self.phase_config)
        droplet.reservoir = Reservoir.WORKING_STREAM
        self.repo.upsert(droplet)
        self.emit.emit(
            EventType.MELTED,
            droplet_id=droplet.id,
            payload={"phase": droplet.phase.value, "reservoir": droplet.reservoir.value},
        )
        return droplet

    # ------------------------------------------------------------------ #
    # 9. FILTER -- clean / verify / reconcile (delegate to contamination).
    # ------------------------------------------------------------------ #
    def filter(self, droplet: Droplet) -> Droplet:
        """Delegate to ``contamination.filter_droplet`` (Track C policy)."""
        if self.contamination is None:
            raise RuntimeError("FILTER requires a contamination module to be injected")
        filtered = self.contamination.filter_droplet(droplet, detector=self.intelligence.detector)
        self.repo.upsert(filtered)
        self.emit.emit(
            EventType.FILTERED,
            droplet_id=filtered.id,
            payload={"phase": filtered.phase.value, "purity": filtered.state.purity},
        )
        return filtered

    # ------------------------------------------------------------------ #
    # 10. POLLUTE -- mark a memory as contaminated (delegate to contamination).
    # ------------------------------------------------------------------ #
    def pollute(self, droplet: Droplet, reason: str) -> Droplet:
        """Delegate to ``contamination.mark_polluted`` (Track C policy)."""
        if self.contamination is None:
            raise RuntimeError("POLLUTE requires a contamination module to be injected")
        polluted = self.contamination.mark_polluted(droplet, reason)
        self.repo.upsert(polluted)
        self.emit.emit(
            EventType.POLLUTED,
            droplet_id=polluted.id,
            payload={"reason": reason, "phase": polluted.phase.value},
        )
        return polluted

    # ------------------------------------------------------------------ #
    # 11. DISTILL -- extract the purest principle from a cluster.
    # ------------------------------------------------------------------ #
    def distill(self, cluster: Sequence[Droplet], *, principle: str | None = None) -> Droplet:
        """Extract a single high-purity principle droplet from a cluster.

        Uses the abstractor to derive the principle text (unless one is given) and
        lands it in the CLOUD reservoir — the top of the abstraction ladder
        (``evaporate``→``condense``→``distill``), reusable by ordinary approved
        agents at recall (ADR-0036). A *distilled* principle is system-derived
        reasoning, distinct from a *user-declared* SACRED anchor (value/vow);
        SACRED stays reserved for the latter (consent-gated). ``phase`` stays
        GROUNDWATER so the principle reads as a settled abstraction and keeps the
        ``abstraction_bonus`` (which is phase-keyed) at recall.
        """
        if not cluster:
            raise ValueError("DISTILL requires at least one droplet")
        members = list(cluster)
        joined = "; ".join(d.content for d in members)
        text = principle or self.intelligence.abstractor.evaporate(joined)
        principle_droplet = Droplet(
            id=new_id(),
            content=text,
            source="distill",
            phase=Phase.GROUNDWATER,
            reservoir=Reservoir.CLOUD,
            state=State(
                purity=clamp_unit(max(d.state.purity for d in members) + 0.05),
                gravity=clamp_unit(max(d.state.gravity for d in members) + 0.1),
                integrity=clamp_unit(max(d.state.integrity for d in members)),
                confidence=clamp_unit(sum(d.state.confidence for d in members) / len(members)),
                depth=0.6,
            ),
            embedding=self.intelligence.embedder.embed(text),
        )
        principle_droplet.meta["principle"] = text
        principle_droplet.meta["distilled_from"] = [d.id for d in members]
        for d in members:
            principle_droplet.links.derived_from.append(d.id)
        self.repo.upsert(principle_droplet)
        for d in members:
            self.repo.add_link(principle_droplet.id, "derived_from", d.id)
        self.emit.emit(
            EventType.DISTILLED,
            droplet_id=principle_droplet.id,
            payload={"distilled_from": [d.id for d in members], "reservoir": principle_droplet.reservoir.value},
        )
        return principle_droplet

    # ------------------------------------------------------------------ #
    # 12. IRRIGATE -- apply a memory pattern to a new task (touch cycle).
    # ------------------------------------------------------------------ #
    def irrigate(self, droplet: Droplet, *, task: str | None = None) -> Droplet:
        """Apply a pattern to a new task; increment the droplet's cycle count."""
        now = datetime.now(UTC)
        self.repo.touch_cycle(droplet.id, recalled=now, increment_count=True)
        droplet.cycle.cycle_count += 1
        droplet.cycle.last_recalled = now
        if task is not None:
            droplet.meta.setdefault("applied_to", []).append(task)
        self.emit.emit(
            EventType.IRRIGATED,
            droplet_id=droplet.id,
            payload={"task": task, "cycle_count": droplet.cycle.cycle_count},
        )
        return droplet

    # ------------------------------------------------------------------ #
    # 13. DRAIN -- reduce salience / active influence (delegate to forgetting).
    # ------------------------------------------------------------------ #
    def drain(self, droplet: Droplet, **kwargs: Any) -> Droplet:
        """Delegate to ``forgetting.drain`` (Track C policy)."""
        if self.forgetting is None:
            raise RuntimeError("DRAIN requires a forgetting module to be injected")
        drained = self.forgetting.drain(droplet, **kwargs)
        self.repo.upsert(drained)
        self.emit.emit(
            EventType.DRAINED,
            droplet_id=drained.id,
            payload={"phase": drained.phase.value, "reservoir": drained.reservoir.value},
        )
        return drained

    # ------------------------------------------------------------------ #
    # 14. ARCHIVE -- move to cold storage (delegate to forgetting sediment/seal).
    # ------------------------------------------------------------------ #
    def archive(self, droplet: Droplet, *, seal: bool = False, **kwargs: Any) -> Droplet:
        """Delegate to ``forgetting.seal`` (when ``seal``) or ``forgetting.sediment``."""
        if self.forgetting is None:
            raise RuntimeError("ARCHIVE requires a forgetting module to be injected")
        if seal:
            archived = self.forgetting.seal(droplet, **kwargs)
        else:
            archived = self.forgetting.sediment(droplet, **kwargs)
        self.repo.upsert(archived)
        self.emit.emit(
            EventType.ARCHIVED,
            droplet_id=archived.id,
            payload={"sealed": bool(seal), "phase": archived.phase.value, "reservoir": archived.reservoir.value},
        )
        return archived

    # ------------------------------------------------------------------ #
    # 15. FORGET -- delete / render inaccessible (governance-checked delete).
    # ------------------------------------------------------------------ #
    def forget(self, droplet: Droplet, *, agent: Any = None, context: Any = None) -> ProtocolResponse:
        """Governance-checked deletion: ``check_access`` then ``forgetting.delete``.

        Returns a :class:`ProtocolResponse` describing the decision/outcome.
        """
        if self.forgetting is None:
            raise RuntimeError("FORGET requires a forgetting module to be injected")

        decision_dict: dict[str, Any] | None = None
        if self.check_access is not None and agent is not None:
            from hydromemory.governance.obligations import Operation

            decision = self.check_access(droplet, agent, context, Operation.MUTATE)
            decision_dict = decision.to_dict() if hasattr(decision, "to_dict") else {"allowed": bool(decision)}
            if not getattr(decision, "allowed", True):
                return ProtocolResponse(
                    operation="FORGET",
                    result=False,
                    decision=decision_dict,
                    outcome={"deleted": False, "droplet_id": droplet.id},
                )

        self.forgetting.delete(droplet)
        self.repo.delete(droplet.id)
        self.emit.emit(
            EventType.FORGOTTEN,
            droplet_id=droplet.id,
            payload={"deleted": True},
        )
        return ProtocolResponse(
            operation="FORGET",
            result=True,
            decision=decision_dict,
            outcome={"deleted": True, "droplet_id": droplet.id},
        )


__all__ = ["Verbs", "RecallMode"]


# Re-derive triggers helper exported for convenience (used by pipeline tests).
def derive_triggers(droplet: Droplet, context: dict[str, Any] | None = None) -> set[Trigger]:
    return detect_triggers(droplet, context or {})
