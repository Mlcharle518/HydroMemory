"""Recall and action layer (PRD §5.6).

Recall is *more* than semantic similarity. The §5.6 formula is::

    recall_score =
        semantic_similarity
      + trigger_similarity
      + contextual_fit
      + pressure
      + gravity
      + phase_accessibility
      + permission_score
      - depth_resistance
      - contamination_penalty
      - privacy_risk

This module owns:

* :func:`hydro_recall_score` -- compute the score. Terms derived here from the
  droplet + ``query_ctx`` (``trigger_similarity``, ``contextual_fit``,
  ``pressure``, ``gravity``, ``phase_accessibility``, ``depth_resistance``); the
  governance/embedding terms (``semantic_similarity``, ``permission_score``,
  ``privacy_risk``, ``contamination_penalty``) are *passed in* by the pipeline.
* :func:`recall_threshold` -- the per-(phase, reservoir) cut-off.
* :class:`RecallMode` + :func:`select_recall_mode` -- the §5.6 recall-mode table.
* :func:`format_recall` -> :class:`RecallResult` -- render a droplet per mode.

Every term is clamped to ``[0, 1]`` before weighting (:class:`RecallWeights`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from hydromemory.reservoirs import Reservoir, behavior_for
from hydromemory.schema import Droplet, Phase, clamp_unit
from hydromemory.triggers import Trigger, detect_triggers

# --- Phase accessibility (PRD §5.4 meaning -> recall readiness) -------------
# How readily a droplet in a given phase surfaces. Liquid is fully active;
# polluted is unusable; deep/frozen phases resist recall. The four transient
# phases (river/snow/fog/steam) appear during recall, so they get values too.
PHASE_ACCESSIBILITY: dict[Phase, float] = {
    Phase.LIQUID: 1.0,
    Phase.RAIN: 0.9,
    Phase.STEAM: 0.85,
    Phase.RIVER: 0.7,
    Phase.VAPOR: 0.6,
    Phase.CLOUD: 0.6,
    Phase.FILTERED: 0.6,
    Phase.SNOW: 0.45,
    Phase.GROUNDWATER: 0.4,
    Phase.FOG: 0.3,
    Phase.OCEAN: 0.3,
    Phase.ICE: 0.2,
    Phase.POLLUTED: 0.0,
}

# --- Recall thresholds (PRD §5.6) -------------------------------------------
# A candidate is recalled when its score exceeds threshold. Deep/frozen/polluted
# phases demand a higher bar; the reservoir adds a small additive adjustment
# (slower reservoirs -> slightly higher bar). Documented defaults.
PHASE_THRESHOLD_BASE: dict[Phase, float] = {
    Phase.LIQUID: 0.30,
    Phase.RAIN: 0.32,
    Phase.STEAM: 0.34,
    Phase.RIVER: 0.38,
    Phase.VAPOR: 0.40,
    Phase.CLOUD: 0.40,
    Phase.FILTERED: 0.42,
    Phase.SNOW: 0.48,
    Phase.GROUNDWATER: 0.55,
    Phase.FOG: 0.55,
    Phase.OCEAN: 0.55,
    Phase.ICE: 0.70,
    Phase.POLLUTED: 0.95,
}

RESERVOIR_THRESHOLD_ADJ: dict[Reservoir, float] = {
    Reservoir.WORKING_STREAM: 0.0,
    Reservoir.SURFACE: 0.02,
    Reservoir.CLOUD: 0.05,
    Reservoir.GROUNDWATER: 0.10,
    Reservoir.OCEAN: 0.12,
    Reservoir.GLACIER: 0.15,
    Reservoir.SACRED: 0.15,
    Reservoir.CONTAMINATED: 0.30,
}


@dataclass
class RecallWeights:
    """Per-term weights for :func:`hydro_recall_score` (all default 1.0)."""

    semantic_similarity: float = 1.0
    trigger_similarity: float = 1.0
    contextual_fit: float = 1.0
    pressure: float = 1.0
    gravity: float = 1.0
    phase_accessibility: float = 1.0
    permission_score: float = 1.0
    depth_resistance: float = 1.0
    contamination_penalty: float = 1.0
    privacy_risk: float = 1.0
    # Bonus for abstracted/derived phases (vapor/cloud/groundwater) so patterns can
    # outrank literal sources. Default 0.0 keeps the literal §5.6 formula unchanged.
    abstraction_bonus: float = 0.0
    # Bonus for a droplet's spreading-activation over the links graph (the §4 spine
    # of docs/closing-the-gaps.md). Default 0.0 keeps recall isolated/§5.6-exact;
    # raising it lets a question pull in the connected constellation (multi-hop).
    activation_bonus: float = 0.0


DEFAULT_RECALL_WEIGHTS = RecallWeights()


def phase_accessibility(droplet: Droplet) -> float:
    """Recall readiness from phase, scaled by the reservoir's behavioral speed."""
    base = PHASE_ACCESSIBILITY.get(droplet.phase, 0.3)
    speed = behavior_for(droplet.reservoir).speed
    # Blend phase readiness with reservoir speed (both in [0,1]).
    return clamp_unit(0.5 * base + 0.5 * speed)


def depth_resistance(droplet: Droplet) -> float:
    """How strongly the memory resists surfacing (directly its ``state.depth``)."""
    return clamp_unit(droplet.state.depth)


# Abstracted / derived phases that the literal-leaning default under-ranks.
_ABSTRACTION_PHASES: frozenset[Phase] = frozenset({Phase.VAPOR, Phase.CLOUD, Phase.GROUNDWATER})


def _abstraction_signal(droplet: Droplet) -> float:
    """1.0 for an abstracted/derived memory (vapor/cloud/groundwater), else 0.0."""
    return 1.0 if droplet.phase in _ABSTRACTION_PHASES else 0.0


def _droplet_triggers(droplet: Droplet) -> set[Trigger]:
    """Triggers recorded on the droplet (meta) or derivable from its state."""
    recorded = droplet.meta.get("triggers")
    out: set[Trigger] = set()
    if isinstance(recorded, (list, tuple, set)):
        for item in recorded:
            try:
                out.add(Trigger(item))
            except ValueError:
                continue
    # Always fold in state-derived triggers (no context) so the set is non-empty.
    out |= detect_triggers(droplet, {})
    return out


def _query_triggers(query_ctx: dict[str, Any]) -> set[Trigger]:
    out: set[Trigger] = set()
    raw = query_ctx.get("triggers")
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            try:
                out.add(Trigger(item))
            except ValueError:
                continue
    single = query_ctx.get("trigger")
    if single is not None:
        try:
            out.add(Trigger(single))
        except ValueError:
            pass
    return out


def trigger_similarity(droplet: Droplet, query_ctx: dict[str, Any]) -> float:
    """Jaccard overlap of the query's triggers with the droplet's triggers."""
    q = _query_triggers(query_ctx)
    if not q:
        return 0.0
    d = _droplet_triggers(droplet)
    if not d:
        return 0.0
    inter = q & d
    union = q | d
    return clamp_unit(len(inter) / len(union)) if union else 0.0


def contextual_fit(droplet: Droplet, query_ctx: dict[str, Any]) -> float:
    """How well the droplet matches the query's topic / session / tags.

    Combines (a) topic/theme match against the droplet's ``semantic_tags`` and
    ``meta['context']`` topic, and (b) session-type match. Returns ``[0, 1]``.
    """
    hits = 0
    total = 0

    drop_ctx = droplet.meta.get("context") if isinstance(droplet.meta.get("context"), dict) else {}
    tags = {str(t).lower() for t in droplet.semantic_tags}

    topic = query_ctx.get("topic") or query_ctx.get("theme")
    if topic is not None:
        total += 1
        topic_l = str(topic).lower()
        drop_topic = str(drop_ctx.get("topic", "")).lower() if drop_ctx else ""
        if topic_l and (topic_l == drop_topic or topic_l in tags or any(topic_l in t or t in topic_l for t in tags)):
            hits += 1

    session = query_ctx.get("session_type") or query_ctx.get("session")
    if session is not None:
        total += 1
        drop_session = str(drop_ctx.get("session_type", "")).lower() if drop_ctx else ""
        if str(session).lower() and str(session).lower() == drop_session:
            hits += 1

    q_tags = query_ctx.get("tags")
    if isinstance(q_tags, (list, tuple, set)):
        total += 1
        q_tags_l = {str(t).lower() for t in q_tags}
        if tags & q_tags_l:
            hits += 1

    if total == 0:
        return 0.0
    score = hits / total
    return clamp_unit(score)


def hydro_recall_score(
    droplet: Droplet,
    query_ctx: dict[str, Any],
    *,
    semantic_similarity: float,
    permission_score: float,
    privacy_risk: float,
    contamination_penalty: float,
    activation: float = 0.0,
    weights: RecallWeights | None = None,
) -> float:
    """Compute the §5.6 recall score for ``droplet`` against ``query_ctx``.

    Derived-here terms: ``trigger_similarity``, ``contextual_fit``, ``pressure``,
    ``gravity``, ``phase_accessibility``, ``depth_resistance``. Passed-in terms:
    ``semantic_similarity``, ``permission_score``, ``privacy_risk``,
    ``contamination_penalty`` (the pipeline passes ``1 - state.purity`` as the
    default ``contamination_penalty``). Each term is clamped to ``[0, 1]`` then
    weighted; positives add, the final three subtract.
    """
    w = weights or DEFAULT_RECALL_WEIGHTS

    sem = clamp_unit(semantic_similarity)
    trig = trigger_similarity(droplet, query_ctx)
    ctx_fit = contextual_fit(droplet, query_ctx)
    pressure = clamp_unit(droplet.state.pressure)
    gravity = clamp_unit(droplet.state.gravity)
    accessibility = phase_accessibility(droplet)
    perm = clamp_unit(permission_score)
    depth = depth_resistance(droplet)
    contam = clamp_unit(contamination_penalty)
    privacy = clamp_unit(privacy_risk)

    return (
        w.semantic_similarity * sem
        + w.trigger_similarity * trig
        + w.contextual_fit * ctx_fit
        + w.pressure * pressure
        + w.gravity * gravity
        + w.phase_accessibility * accessibility
        + w.permission_score * perm
        - w.depth_resistance * depth
        - w.contamination_penalty * contam
        - w.privacy_risk * privacy
        + w.abstraction_bonus * _abstraction_signal(droplet)
        + w.activation_bonus * clamp_unit(activation)
    )


def recall_threshold(phase: Phase, reservoir: Reservoir) -> float:
    """Phase-base threshold plus a reservoir additive adjustment (documented)."""
    base = PHASE_THRESHOLD_BASE.get(phase, 0.5)
    adj = RESERVOIR_THRESHOLD_ADJ.get(reservoir, 0.0)
    return base + adj


# --- Recall modes (PRD §5.6) ------------------------------------------------
class RecallMode(str, Enum):
    LITERAL = "literal"            # use exact stored content
    PATTERN = "pattern"            # use abstracted meaning
    BEHAVIORAL = "behavioral"      # adapt behavior without quoting memory
    WARNING = "warning"            # surface a risk, contradiction, or boundary
    SILENT = "silent"              # guide behavior without mentioning memory
    USER_VISIBLE = "user_visible"  # tell the user what memory is being used
    REFLECTIVE = "reflective"      # ask whether memory is still accurate


def select_recall_mode(droplet: Droplet, context: dict[str, Any] | None = None) -> RecallMode:
    """Pick a recall mode from droplet state + context (rule-based, §5.6).

    Precedence (first match wins):
      1. WARNING      -- contradiction or contamination present.
      2. SILENT       -- high-sensitivity *and* private memory.
      3. REFLECTIVE   -- low confidence, or a FOG (ambiguous) phase.
      4. USER_VISIBLE -- the user explicitly asks what is known.
      5. LITERAL      -- an exact-quote request.
      6. PATTERN      -- abstract phases (VAPOR/CLOUD).
      7. BEHAVIORAL   -- identity-relevant memory (default for actionable recall).
    """
    ctx = dict(context or {})
    s = droplet.state

    # 1. Warning: contradictions or contaminated/low-purity memory.
    contaminated = (
        droplet.phase is Phase.POLLUTED
        or droplet.reservoir is Reservoir.CONTAMINATED
        or bool(droplet.links.contradictions)
        or bool(ctx.get("contradiction"))
        or bool(droplet.meta.get("requires_filtering"))
    )
    if contaminated:
        return RecallMode.WARNING

    # 2. Silent: sensitive + private -> guide without mentioning.
    sensitivity = _meta_float(droplet, "sensitivity")
    is_private = droplet.permissions.visibility.value == "private"
    if sensitivity >= 0.7 and is_private:
        return RecallMode.SILENT

    # 3. Reflective: low confidence or ambiguous (fog) recall.
    if s.confidence <= 0.3 or droplet.phase is Phase.FOG:
        return RecallMode.REFLECTIVE

    # 4. User-visible: explicit "what do you know" style ask.
    if _ctx_truthy(ctx, "what_do_you_know", "user_visible", "show_memory") or _intent_is(
        ctx, "what_do_you_know", "list_memory"
    ):
        return RecallMode.USER_VISIBLE

    # 5. Literal: exact-quote request.
    if _ctx_truthy(ctx, "exact_quote", "verbatim", "literal") or _intent_is(ctx, "exact_quote", "quote"):
        return RecallMode.LITERAL

    # 6. Pattern: abstracted phases.
    if droplet.phase in (Phase.VAPOR, Phase.CLOUD):
        return RecallMode.PATTERN

    # 7. Behavioral: identity-relevant / default actionable recall.
    return RecallMode.BEHAVIORAL


def _meta_float(droplet: Droplet, key: str) -> float:
    v = droplet.meta.get(key)
    if v is None:
        ctx = droplet.meta.get("context")
        if isinstance(ctx, dict):
            v = ctx.get(key)
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _ctx_truthy(ctx: dict[str, Any], *keys: str) -> bool:
    return any(bool(ctx.get(k)) for k in keys)


def _intent_is(ctx: dict[str, Any], *values: str) -> bool:
    intent = str(ctx.get("intent", "")).lower()
    return intent in {v.lower() for v in values}


@dataclass
class RecallResult:
    """Rendered output of a recall (PRD §5.6 action layer)."""

    mode: RecallMode
    surface_text: str
    internal_guidance: str
    show_to_user: bool
    explanation: str
    droplet_id: str = ""
    score: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


def format_recall(
    droplet: Droplet,
    mode: RecallMode,
    *,
    score: float = 0.0,
) -> RecallResult:
    """Render a droplet for the chosen recall ``mode`` (fields differ per mode)."""
    content = droplet.content
    pattern = str(droplet.meta.get("pattern") or droplet.meta.get("essence") or content)

    if mode is RecallMode.LITERAL:
        return RecallResult(
            mode=mode,
            surface_text=content,
            internal_guidance=f"Quote stored content verbatim: {content!r}",
            show_to_user=True,
            explanation="Using exact stored content (literal recall).",
            droplet_id=droplet.id,
            score=score,
        )
    if mode is RecallMode.PATTERN:
        return RecallResult(
            mode=mode,
            surface_text=pattern,
            internal_guidance=f"Apply abstracted pattern: {pattern!r}",
            show_to_user=False,
            explanation="Using abstracted meaning rather than the exact memory (pattern recall).",
            droplet_id=droplet.id,
            score=score,
        )
    if mode is RecallMode.BEHAVIORAL:
        return RecallResult(
            mode=mode,
            surface_text="",
            internal_guidance=f"Adapt behavior to: {pattern!r} (do not quote the memory).",
            show_to_user=False,
            explanation="Adapting behavior without quoting the memory (behavioral recall).",
            droplet_id=droplet.id,
            score=score,
        )
    if mode is RecallMode.WARNING:
        reason = str(droplet.meta.get("reason") or "contradiction or contamination detected")
        return RecallResult(
            mode=mode,
            surface_text=f"Caution: {reason}",
            internal_guidance=f"Surface a risk/boundary; memory may be unreliable: {reason}",
            show_to_user=True,
            explanation=f"Surfacing a risk/contradiction (warning recall): {reason}",
            droplet_id=droplet.id,
            score=score,
        )
    if mode is RecallMode.SILENT:
        return RecallResult(
            mode=mode,
            surface_text="",
            internal_guidance=f"Guide behavior silently from: {pattern!r}. Do not mention this memory.",
            show_to_user=False,
            explanation="Guiding behavior without explicitly mentioning the memory (silent recall).",
            droplet_id=droplet.id,
            score=score,
        )
    if mode is RecallMode.USER_VISIBLE:
        return RecallResult(
            mode=mode,
            surface_text=f"I'm drawing on what I know: {content}",
            internal_guidance=f"Tell the user this memory is in use: {content!r}",
            show_to_user=True,
            explanation="Telling the user which memory is being used (user-visible recall).",
            droplet_id=droplet.id,
            score=score,
        )
    # REFLECTIVE
    return RecallResult(
        mode=mode,
        surface_text=f"I recall: {content}. Is that still accurate?",
        internal_guidance=f"Ask the user to confirm the memory is current: {content!r}",
        show_to_user=True,
        explanation="Asking whether the memory is still accurate (reflective recall).",
        droplet_id=droplet.id,
        score=score,
    )
