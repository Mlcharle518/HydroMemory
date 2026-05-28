"""Track B: recall scoring, thresholds, modes, and rendering (PRD §5.6)."""
from __future__ import annotations

import pytest

from hydromemory.recall import (
    PHASE_THRESHOLD_BASE,
    RecallMode,
    RecallWeights,
    contextual_fit,
    depth_resistance,
    format_recall,
    hydro_recall_score,
    phase_accessibility,
    recall_threshold,
    select_recall_mode,
    trigger_similarity,
)
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Permissions, Phase, State, Visibility


def _known_droplet() -> Droplet:
    d = Droplet(
        id="g",
        phase=Phase.LIQUID,
        reservoir=Reservoir.WORKING_STREAM,
        semantic_tags=["AI memory", "architecture"],
        state=State(pressure=0.5, gravity=0.4, depth=0.2, purity=0.9),
    )
    d.meta["context"] = {"topic": "AI memory", "session_type": "design"}
    d.meta["triggers"] = ["gravity"]
    return d


# --- Component terms --------------------------------------------------------
def test_trigger_similarity_full_overlap():
    d = _known_droplet()
    assert trigger_similarity(d, {"triggers": ["gravity"]}) == 1.0


def test_trigger_similarity_no_query_triggers_is_zero():
    assert trigger_similarity(_known_droplet(), {}) == 0.0


def test_contextual_fit_topic_and_session():
    d = _known_droplet()
    assert contextual_fit(d, {"topic": "AI memory", "session_type": "design"}) == 1.0


def test_contextual_fit_no_signals_is_zero():
    assert contextual_fit(_known_droplet(), {}) == 0.0


def test_phase_accessibility_liquid_working_stream_is_one():
    assert phase_accessibility(_known_droplet()) == 1.0


def test_phase_accessibility_polluted_contaminated_is_zero():
    d = Droplet(id="p", phase=Phase.POLLUTED, reservoir=Reservoir.CONTAMINATED)
    assert phase_accessibility(d) == 0.0


def test_depth_resistance_reads_state_depth():
    assert depth_resistance(_known_droplet()) == 0.2


# --- Golden score -----------------------------------------------------------
def test_recall_score_golden_value():
    d = _known_droplet()
    qctx = {"topic": "AI memory", "session_type": "design", "triggers": ["gravity"]}
    score = hydro_recall_score(
        d,
        qctx,
        semantic_similarity=0.8,
        permission_score=1.0,
        privacy_risk=0.1,
        contamination_penalty=0.1,
    )
    # 0.8 + 1.0 + 1.0 + 0.5 + 0.4 + 1.0 + 1.0 - 0.2 - 0.1 - 0.1 = 5.3
    assert score == pytest.approx(5.3)


def test_recall_score_weights_zero_out_terms():
    d = _known_droplet()
    qctx = {"triggers": ["gravity"]}
    w = RecallWeights(
        semantic_similarity=0.0,
        trigger_similarity=0.0,
        contextual_fit=0.0,
        pressure=0.0,
        gravity=0.0,
        phase_accessibility=0.0,
        permission_score=0.0,
        depth_resistance=0.0,
        contamination_penalty=0.0,
        privacy_risk=0.0,
    )
    score = hydro_recall_score(
        d, qctx, semantic_similarity=1.0, permission_score=1.0, privacy_risk=1.0,
        contamination_penalty=1.0, weights=w,
    )
    assert score == 0.0


def test_passed_in_terms_are_clamped():
    d = _known_droplet()
    # privacy_risk > 1 is clamped to 1 before subtracting.
    s_over = hydro_recall_score(d, {}, semantic_similarity=0.0, permission_score=0.0,
                                privacy_risk=5.0, contamination_penalty=0.0)
    s_one = hydro_recall_score(d, {}, semantic_similarity=0.0, permission_score=0.0,
                               privacy_risk=1.0, contamination_penalty=0.0)
    assert s_over == s_one


# --- Thresholds -------------------------------------------------------------
def test_recall_threshold_table():
    assert recall_threshold(Phase.LIQUID, Reservoir.WORKING_STREAM) == pytest.approx(0.30)
    assert recall_threshold(Phase.GROUNDWATER, Reservoir.GLACIER) == pytest.approx(0.70)
    assert recall_threshold(Phase.POLLUTED, Reservoir.CONTAMINATED) == pytest.approx(1.25)


def test_threshold_monotonic_phase_base():
    assert PHASE_THRESHOLD_BASE[Phase.LIQUID] < PHASE_THRESHOLD_BASE[Phase.GROUNDWATER]
    assert PHASE_THRESHOLD_BASE[Phase.GROUNDWATER] < PHASE_THRESHOLD_BASE[Phase.ICE]
    assert PHASE_THRESHOLD_BASE[Phase.ICE] < PHASE_THRESHOLD_BASE[Phase.POLLUTED]


# --- Recall modes (all 7) ---------------------------------------------------
def test_mode_warning_on_contamination():
    d = Droplet(id="w", phase=Phase.POLLUTED)
    assert select_recall_mode(d, {}) is RecallMode.WARNING


def test_mode_warning_on_contradiction_links():
    d = Droplet(id="w", phase=Phase.LIQUID)
    d.links.contradictions.append("other")
    assert select_recall_mode(d, {}) is RecallMode.WARNING


def test_mode_silent_on_sensitive_private():
    d = Droplet(id="s", phase=Phase.LIQUID, permissions=Permissions(visibility=Visibility.PRIVATE))
    d.meta["sensitivity"] = 0.9
    assert select_recall_mode(d, {}) is RecallMode.SILENT


def test_mode_reflective_on_low_confidence():
    d = Droplet(id="r", phase=Phase.LIQUID, state=State(confidence=0.1))
    assert select_recall_mode(d, {}) is RecallMode.REFLECTIVE


def test_mode_reflective_on_fog():
    d = Droplet(id="r", phase=Phase.FOG, state=State(confidence=0.9))
    assert select_recall_mode(d, {}) is RecallMode.REFLECTIVE


def test_mode_user_visible_on_explicit_ask():
    d = Droplet(id="u", phase=Phase.LIQUID, state=State(confidence=0.9))
    assert select_recall_mode(d, {"what_do_you_know": True}) is RecallMode.USER_VISIBLE


def test_mode_literal_on_exact_quote():
    d = Droplet(id="l", phase=Phase.LIQUID, state=State(confidence=0.9))
    assert select_recall_mode(d, {"exact_quote": True}) is RecallMode.LITERAL


def test_mode_pattern_on_vapor():
    d = Droplet(id="p", phase=Phase.VAPOR, state=State(confidence=0.9))
    assert select_recall_mode(d, {}) is RecallMode.PATTERN


def test_mode_behavioral_default():
    d = Droplet(id="b", phase=Phase.LIQUID, state=State(confidence=0.9))
    assert select_recall_mode(d, {}) is RecallMode.BEHAVIORAL


# --- format_recall for all 7 modes -----------------------------------------
@pytest.mark.parametrize(
    "mode,show",
    [
        (RecallMode.LITERAL, True),
        (RecallMode.PATTERN, False),
        (RecallMode.BEHAVIORAL, False),
        (RecallMode.WARNING, True),
        (RecallMode.SILENT, False),
        (RecallMode.USER_VISIBLE, True),
        (RecallMode.REFLECTIVE, True),
    ],
)
def test_format_recall_each_mode(mode, show):
    d = _known_droplet()
    d.content = "User prefers depth."
    result = format_recall(d, mode, score=2.5)
    assert result.mode is mode
    assert result.show_to_user is show
    assert result.explanation  # non-empty
    assert result.internal_guidance  # non-empty
    assert result.droplet_id == "g"
    assert result.score == 2.5


def test_format_recall_literal_quotes_content():
    d = _known_droplet()
    d.content = "exact words"
    result = format_recall(d, RecallMode.LITERAL)
    assert "exact words" in result.surface_text


def test_format_recall_warning_includes_reason():
    d = _known_droplet()
    d.meta["reason"] = "contradicts a prior memory"
    result = format_recall(d, RecallMode.WARNING)
    assert "contradicts a prior memory" in result.surface_text
