"""Track C contamination tests (PRD §10.1): mark / assess+route / filter.

Uses a fake :class:`ContaminationDetector` (the only Track-A dependency, mocked
via its frozen ABC) so these tests never touch a real NLP backend.
"""
from __future__ import annotations

from typing import Any

from hydromemory.contamination import (
    FILTERED_PURITY,
    assess_and_route,
    filter_droplet,
    mark_polluted,
)
from hydromemory.intelligence.base import ContaminationDetector, ContaminationVerdict
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, State


class FakeDetector(ContaminationDetector):
    """Returns a pre-programmed verdict and records the calls it received."""

    def __init__(self, verdict: ContaminationVerdict) -> None:
        self.verdict = verdict
        self.calls: list[tuple[Droplet, dict[str, Any]]] = []

    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        self.calls.append((droplet, context))
        return self.verdict


def make_droplet(
    phase: Phase = Phase.LIQUID,
    reservoir: Reservoir = Reservoir.WORKING_STREAM,
    purity: float = 0.4,
    confidence: float = 0.8,
) -> Droplet:
    return Droplet(
        id="mem_7712",
        content="User hates working with teams.",
        phase=phase,
        reservoir=reservoir,
        state=State(purity=purity, confidence=confidence),
    )


def test_mark_polluted_routes_and_disables():
    d = make_droplet()
    out = mark_polluted(d, reason="Low confidence inference from emotional conversation.")
    assert out is d
    assert out.phase is Phase.POLLUTED
    assert out.reservoir is Reservoir.CONTAMINATED
    assert out.meta["reason"].startswith("Low confidence")
    assert out.meta["usable_for_generation"] is False
    assert out.meta["requires_filtering"] is True
    assert out.state.confidence <= 0.3  # contaminated -> confidence not trusted


def test_assess_and_route_when_contaminated():
    detector = FakeDetector(ContaminationVerdict(True, "manipulated input", 0.77))
    d = make_droplet()
    out = assess_and_route(d, {"topic": "teams"}, detector)
    assert out.phase is Phase.POLLUTED
    assert out.reservoir is Reservoir.CONTAMINATED
    assert out.meta["usable_for_generation"] is False
    assert out.meta["contamination_confidence"] == 0.77
    assert out.meta["contamination_checked"] is True
    # the detector actually saw our droplet + context.
    assert detector.calls and detector.calls[0][1] == {"topic": "teams"}


def test_assess_and_route_when_clean_leaves_droplet():
    detector = FakeDetector(ContaminationVerdict(False, "looks fine", 0.9))
    d = make_droplet()
    out = assess_and_route(d, {}, detector)
    assert out.phase is Phase.LIQUID
    assert out.reservoir is Reservoir.WORKING_STREAM
    assert out.meta["contamination_checked"] is True
    assert "usable_for_generation" not in out.meta  # untouched
    assert detector.calls  # detector was still consulted


def test_filter_droplet_reframes_polluted_to_filtered():
    # Start from an already-polluted droplet (as Example E does).
    d = mark_polluted(make_droplet(), reason="agent inferred too much")
    out = filter_droplet(d)
    assert out is d
    assert out.phase is Phase.FILTERED
    assert out.reservoir is Reservoir.SURFACE  # out of the contaminated pool
    assert out.state.purity >= FILTERED_PURITY
    assert out.meta["usable_for_generation"] is True
    assert out.meta["requires_filtering"] is False
    assert out.meta["filtered"] is True
    # the original pollution reason is preserved for audit.
    assert out.meta["reframed_from"] == "agent inferred too much"


def test_filter_does_not_lower_existing_high_purity():
    d = mark_polluted(make_droplet(purity=0.98), reason="r")
    out = filter_droplet(d)
    assert out.state.purity == 0.98  # not pulled down to the floor


def test_filter_accepts_optional_detector():
    detector = FakeDetector(ContaminationVerdict(False, "ok", 1.0))
    d = mark_polluted(make_droplet(), reason="r")
    out = filter_droplet(d, detector)
    assert out.phase is Phase.FILTERED
