"""End-to-end orchestration (PRD §14).

Two entry points wire the engine together:

* :func:`process_experience` -- the §14 capture pipeline: embed + encode a
  droplet, classify, assign phase (LIQUID), route to a reservoir, find related
  droplets, create flow edges, detect triggers, apply phase transitions, run a
  governance memory-policy review, store if allowed, and return a decision dict.
* :func:`recall_for_agent` -- the §14 recall pipeline: embed the query, fetch
  permission-gated candidates, score each with :func:`hydro_recall_score`, keep
  those above :func:`recall_threshold`, rank, and render with
  :func:`format_recall` using the selected recall mode.

Everything is dependency-injected (``repo``, ``intelligence``, ``check_access``
and the governance scorers) so the pipeline is unit-testable with fakes.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hydromemory.bus.emit import NULL_EMITTER, Emitter
from hydromemory.bus.events import EventType
from hydromemory.governance import (
    AccessContext,
    AgentIdentity,
)
from hydromemory.governance import (
    check_access as default_check_access,
)
from hydromemory.governance.obligations import Operation
from hydromemory.intelligence import Intelligence
from hydromemory.phases import (
    DEFAULT_PHASE_CONFIG,
    PhaseConfig,
    apply_phase_transitions,
    assign_initial_phase,
)
from hydromemory.recall import (
    RecallWeights,
    format_recall,
    hydro_recall_score,
    recall_threshold,
    select_recall_mode,
)
from hydromemory.reservoirs import Reservoir, normalize_reservoir
from hydromemory.schema import STORABLE_PHASES, TRANSIENT_PHASES, Droplet, Phase, State, new_id
from hydromemory.storage.repository import DropletRepository
from hydromemory.triggers import detect_triggers

# Where each TRANSIENT phase settles when it would otherwise be persisted as a
# resting state (ADR-0003: only STORABLE_PHASES may be written). The mapping
# follows the §5.4 hydraulic lifecycle downstream: a RIVER's next storable stage is
# GROUNDWATER (River + REPETITION -> Groundwater). SNOW/FOG/STEAM have no
# downstream storable transition, so they fall back to LIQUID (the entry state).
_TRANSIENT_SETTLING: dict[Phase, Phase] = {
    Phase.RIVER: Phase.GROUNDWATER,
    Phase.SNOW: Phase.LIQUID,
    Phase.FOG: Phase.LIQUID,
    Phase.STEAM: Phase.LIQUID,
}


def _settle_transient_phase(phase: Phase) -> Phase:
    """Map a TRANSIENT resting phase to its nearest STORABLE phase (ADR-0003).

    A storable phase is returned unchanged. Any transient phase is settled via
    :data:`_TRANSIENT_SETTLING`, defaulting to LIQUID for completeness.
    """
    if phase in STORABLE_PHASES:
        return phase
    return _TRANSIENT_SETTLING.get(phase, Phase.LIQUID)


# --- Governance scorers: import if Track C has exported them, else fall back.
def _load_governance_scorers() -> tuple[Callable[..., float], Callable[..., float]]:
    try:
        from hydromemory.governance import permission_score as _ps  # type: ignore[attr-defined]
        from hydromemory.governance import privacy_risk as _pr  # type: ignore[attr-defined]

        return _ps, _pr
    except Exception:  # noqa: BLE001 -- Track C may not be present yet.
        return _fallback_permission_score, _fallback_privacy_risk


def _fallback_permission_score(droplet: Droplet, agent: Any) -> float:
    name = getattr(agent, "name", agent)
    if droplet.permissions.visibility.value == "public":
        return 1.0
    allowed = droplet.permissions.allowed_agents
    return 1.0 if (not allowed or name in allowed) else 0.0


def _fallback_privacy_risk(droplet: Droplet, context: Any = None) -> float:
    risk = 0.4 if droplet.permissions.visibility.value == "private" else 0.0
    sensitivity = droplet.meta.get("sensitivity")
    try:
        if sensitivity is not None:
            risk += 0.5 * float(sensitivity)
    except (TypeError, ValueError):
        pass
    return max(0.0, min(1.0, risk))


# --- Reservoir routing (PRD §5.3 / §14 "route_to_reservoir") ----------------
def route_to_reservoir(
    droplet: Droplet,
    classification_sensitivity: float = 0.0,
    *,
    default: Reservoir = Reservoir.WORKING_STREAM,
) -> Reservoir:
    """Pick the home reservoir for a freshly captured droplet.

    Highly sensitive / identity-relevant memory -> SACRED; contaminated -> the
    contaminated pool; otherwise it lands in the fast WORKING_STREAM. (Deeper
    routing happens later via INFILTRATE/FREEZE.)
    """
    if droplet.phase is Phase.POLLUTED or droplet.meta.get("requires_filtering"):
        return Reservoir.CONTAMINATED
    if classification_sensitivity >= 0.85 or droplet.state.gravity >= 0.9:
        return Reservoir.SACRED
    return default


def _estimate_state(event: dict[str, Any], classification: Any) -> State:
    """Seed a State from an event's explicit ``state``/floats + classification."""
    state_src: dict[str, Any] = dict(event.get("state") or {})
    for f in (
        "temperature",
        "pressure",
        "gravity",
        "purity",
        "salinity",
        "depth",
        "fluidity",
        "integrity",
        "confidence",
        "emotional_charge",
        "charge",
    ):
        if f in event:
            state_src.setdefault(f, event[f])
    state = State.from_dict(state_src)
    # Classification importance/sensitivity nudge gravity/confidence if unset.
    if state.gravity == 0.0 and getattr(classification, "importance", None) is not None:
        state.gravity = float(classification.importance)
    if state.confidence == 0.0 and getattr(classification, "importance", None) is not None:
        state.confidence = float(classification.importance)
    return state.clamped()


def process_experience(
    event: dict[str, Any],
    user_context: dict[str, Any],
    *,
    repo: DropletRepository,
    intelligence: Intelligence,
    check_access: Callable[..., Any] = default_check_access,
    phase_config: PhaseConfig = DEFAULT_PHASE_CONFIG,
    agent: AgentIdentity | None = None,
    k_related: int = 5,
    emit: Emitter = NULL_EMITTER,
    default_reservoir: Reservoir = Reservoir.WORKING_STREAM,
) -> dict[str, Any]:
    """Run the §14 capture pipeline; return a decision dict.

    The decision dict contains ``store`` (bool), the resulting ``droplet`` (its
    ``to_dict`` view), the fired ``triggers``, ``related`` ids, and the policy
    ``decision`` (when a review ran).
    """
    ctx = dict(user_context or {})

    # 1-2. Capture + encode droplet.
    content = str(event.get("content", ""))
    embedding = intelligence.embedder.embed(content)

    # 3. Classify sensitivity + memory type.
    classification = intelligence.classifier.classify(content)

    # Estimate the initial state vector.
    state = _estimate_state(event, classification)

    droplet = Droplet(
        id=str(event.get("id") or new_id()),
        content=content,
        source=str(event.get("source", "experience")),
        state=state,
        embedding=embedding,
        memory_type=classification.memory_type,
    )
    if ctx:
        droplet.meta["context"] = ctx
    droplet.meta["importance"] = classification.importance
    droplet.meta["sensitivity"] = classification.sensitivity
    droplet.meta["expected_lifespan"] = classification.expected_lifespan

    # 4. Assign phase (LIQUID entry).
    assign_initial_phase(droplet)

    # 5. Assign reservoir.
    if event.get("reservoir"):
        droplet.reservoir = normalize_reservoir(event["reservoir"])
    else:
        droplet.reservoir = route_to_reservoir(
            droplet, classification.sensitivity, default=default_reservoir
        )

    # 6. Link to existing memories (semantic neighbours).
    related: list[str] = []
    try:
        neighbours = repo.search_similar(embedding, k=k_related)
        related = [rid for rid, _ in neighbours if rid != droplet.id]
    except Exception:  # noqa: BLE001 -- a bare/fake repo may not implement search.
        related = []

    # 7 (create flow edges) -- association links to the related droplets.
    for rid in related:
        droplet.links.associations.append(rid)
        try:
            repo.add_link(droplet.id, "associations", rid)
        except Exception:  # noqa: BLE001
            pass

    # 8. Detect triggers.
    trigger_ctx = dict(ctx)
    trigger_ctx.setdefault("cycle_count", droplet.cycle.cycle_count)
    triggers = detect_triggers(droplet, trigger_ctx)
    droplet.meta["triggers"] = sorted(t.value for t in triggers)

    # 9. Transform phase if needed (apply fired triggers in priority order).
    transition_ctx = dict(ctx)
    transition_ctx.setdefault("cycle_count", droplet.cycle.cycle_count)
    apply_phase_transitions(droplet, triggers, transition_ctx, phase_config)

    # 9b. Settle a TRANSIENT resting phase to its nearest storable phase before
    # persisting: a fresh droplet can walk LIQUID->...->RIVER in one pass but the
    # RIVER->GROUNDWATER hop needs cycle_count>=3, leaving RIVER as the rest state
    # -- which ADR-0003 forbids on write (STORABLE_PHASES only).
    if droplet.phase in TRANSIENT_PHASES:
        droplet.phase = _settle_transient_phase(droplet.phase)

    # 10. Memory policy review (governance) -- may block the write.
    store = True
    decision_payload: dict[str, Any] | None = None
    review_agent = agent or AgentIdentity(name=str(ctx.get("agent", "capture_agent")))
    access_ctx = _as_access_context(ctx)
    try:
        decision = check_access(droplet, review_agent, access_ctx, Operation.MUTATE)
        decision_payload = decision.to_dict() if hasattr(decision, "to_dict") else {"allowed": bool(decision)}
        store = bool(getattr(decision, "allowed", True))
    except NotImplementedError:
        # Track C not wired yet -> default-allow but record that review was skipped.
        decision_payload = {"allowed": True, "review": "skipped (governance not implemented)"}
    except Exception as exc:  # noqa: BLE001
        decision_payload = {"allowed": True, "review": f"error: {exc}"}

    # 11. Store with permissions if the decision allows.
    if store:
        repo.upsert(droplet)
        emit.emit(
            EventType.ABSORBED,
            droplet_id=droplet.id,
            payload={
                "phase": droplet.phase.value,
                "reservoir": droplet.reservoir.value,
                "memory_type": droplet.memory_type,
                "source": droplet.source,
            },
        )

    return {
        "store": store,
        "stored": store,
        "droplet": droplet.to_dict(),
        "droplet_id": droplet.id,
        "phase": droplet.phase.value,
        "reservoir": droplet.reservoir.value,
        "triggers": sorted(t.value for t in triggers),
        "related": related,
        "decision": decision_payload,
    }


def _as_access_context(ctx: dict[str, Any]) -> AccessContext:
    return AccessContext(
        recall_mode=ctx.get("recall_mode"),
        safe_context=bool(ctx.get("safe_context") or ctx.get("safe")),
        consent_granted=bool(ctx.get("consent_granted")),
        thaw_granted=bool(ctx.get("thaw_granted")),
    )


def recall_for_agent(
    query: str,
    agent: AgentIdentity,
    context: AccessContext | dict[str, Any],
    *,
    repo: DropletRepository,
    intelligence: Intelligence,
    permission_score: Callable[..., float] | None = None,
    privacy_risk: Callable[..., float] | None = None,
    weights: RecallWeights | None = None,
    k: int = 10,
    query_ctx: dict[str, Any] | None = None,
    emit: Emitter = NULL_EMITTER,
) -> list[Any]:
    """Run the §14 recall pipeline; return ranked :class:`RecallResult` objects.

    Steps: embed the query -> ``repo.search_similar`` with a permission gate ->
    compute ``permission_score`` / ``privacy_risk`` / ``semantic_similarity`` per
    candidate -> :func:`hydro_recall_score` -> keep if ``> recall_threshold`` ->
    rank -> :func:`format_recall` with :func:`select_recall_mode`.
    """
    default_ps, default_pr = _load_governance_scorers()
    ps = permission_score or default_ps
    pr = privacy_risk or default_pr

    access_ctx = context if isinstance(context, AccessContext) else _as_access_context(dict(context or {}))
    qctx: dict[str, Any] = dict(query_ctx or {})
    if isinstance(context, dict):
        # Allow topic/trigger/session signals to ride in via a plain dict context.
        for key in ("topic", "theme", "session_type", "session", "trigger", "triggers", "tags", "intent"):
            if key in context and key not in qctx:
                qctx[key] = context[key]

    embedding = intelligence.embedder.embed(query)

    def gate(d: Droplet) -> bool:
        return float(ps(d, agent)) > 0.0

    hits = repo.search_similar(embedding, k=k, candidate_filter=gate)

    scored: list[Any] = []
    for droplet_id, cosine in hits:
        droplet = repo.get(droplet_id)
        if droplet is None:
            continue
        perm = float(ps(droplet, agent))
        privacy = float(pr(droplet, access_ctx))
        contamination_penalty = 1.0 - droplet.state.purity
        score = hydro_recall_score(
            droplet,
            qctx,
            semantic_similarity=cosine,
            permission_score=perm,
            privacy_risk=privacy,
            contamination_penalty=contamination_penalty,
            weights=weights,
        )
        if score <= recall_threshold(droplet.phase, droplet.reservoir):
            continue
        mode = select_recall_mode(droplet, qctx)
        result = format_recall(droplet, mode, score=score)
        scored.append(result)
        # Recall is itself a cycle event.
        try:
            repo.touch_cycle(droplet.id, recalled=datetime.now(UTC))
        except Exception:  # noqa: BLE001
            pass

    scored.sort(key=lambda r: r.score, reverse=True)
    agent_name = getattr(agent, "name", agent)
    for result in scored:
        emit.emit(
            EventType.RECALLED,
            droplet_id=getattr(result, "droplet_id", None),
            payload={"query": query, "agent": agent_name, "score": getattr(result, "score", None)},
        )
    return scored


__all__ = ["process_experience", "recall_for_agent", "route_to_reservoir"]
