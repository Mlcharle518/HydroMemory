"""Contamination detection and routing (PRD §10.1).

A droplet is *polluted* when its source is unreliable, it contradicts verified
facts, the user later corrects it, an agent inferred too much, the input may be
manipulated, or it is emotionally intense but factually uncertain (§10.1).

The flow mirrors §12 Example E ("Polluted memory becomes filtered memory"):

* :func:`mark_polluted` — stamp a droplet as polluted and route it to the
  ``contaminated`` reservoir, marking it unusable for generation.
* :func:`assess_and_route` — run an injected :class:`ContaminationDetector` and,
  if it returns a contaminated verdict, mark the droplet polluted.
* :func:`filter_droplet` — the Filtration agent's repair step: flip
  ``polluted -> filtered``, raise purity, and record the reframe so the memory
  re-enters circulation as a softened, qualified statement.

All functions mutate and return the same :class:`Droplet` (no copy), matching the
in-place transform style of the forgetting verbs.
"""
from __future__ import annotations

from typing import Any

from hydromemory.intelligence.base import ContaminationDetector
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase

# Purity floor a filtered droplet is raised to once reframed (§12 Example F uses
# 0.92 for a resolved/filtered memory; we never *lower* an already-purer value).
FILTERED_PURITY: float = 0.92


def mark_polluted(droplet: Droplet, reason: str) -> Droplet:
    """Mark ``droplet`` as polluted and route it to the contaminated reservoir.

    Sets ``phase=polluted``, ``reservoir=contaminated``, records ``reason`` and
    ``requires_filtering=True`` in ``meta``, and flips
    ``meta['usable_for_generation']`` to ``False`` so governance refuses to use
    it in responses until it is filtered.
    """
    droplet.phase = Phase.POLLUTED
    droplet.reservoir = Reservoir.CONTAMINATED
    droplet.meta["reason"] = reason
    droplet.meta["usable_for_generation"] = False
    droplet.meta["requires_filtering"] = True
    # Contaminated memory is unreliable: confidence is not trustworthy.
    droplet.state.confidence = min(droplet.state.confidence, 0.3)
    return droplet


def assess_and_route(
    droplet: Droplet,
    context: dict[str, Any],
    detector: ContaminationDetector,
) -> Droplet:
    """Assess ``droplet`` with ``detector`` and route it if contaminated.

    On a contaminated verdict the droplet is sent through :func:`mark_polluted`
    (reservoir/phase/usability), and the detector's confidence is recorded under
    ``meta['contamination_confidence']``. A clean verdict leaves the droplet
    untouched but stamps ``meta['contamination_checked']=True`` for auditability.
    """
    verdict = detector.assess(droplet, context)
    droplet.meta["contamination_checked"] = True
    if verdict.contaminated:
        mark_polluted(droplet, verdict.reason)
        droplet.meta["contamination_confidence"] = float(verdict.confidence)
    return droplet


def filter_droplet(
    droplet: Droplet,
    detector: ContaminationDetector | None = None,
) -> Droplet:
    """Filter a polluted droplet into a usable, reframed ``filtered`` droplet.

    Flips ``polluted -> filtered``, moves it out of the contaminated reservoir
    (to ``surface`` so it can be recalled again), raises purity to at least
    :data:`FILTERED_PURITY`, and clears the ``usable_for_generation`` /
    ``requires_filtering`` flags. The original pollution ``reason`` is preserved
    under ``meta['reframed_from']`` so the repair is auditable. ``detector`` is
    accepted for symmetry / future re-assessment but is not required.
    """
    prior_reason = droplet.meta.get("reason")
    if prior_reason is not None:
        droplet.meta["reframed_from"] = prior_reason

    droplet.phase = Phase.FILTERED
    # Only relocate out of the contaminated pool; respect an already-assigned
    # destination reservoir if a caller pre-set one that is not contaminated.
    if droplet.reservoir is Reservoir.CONTAMINATED:
        droplet.reservoir = Reservoir.SURFACE

    droplet.state.purity = max(droplet.state.purity, FILTERED_PURITY)
    droplet.meta["usable_for_generation"] = True
    droplet.meta["requires_filtering"] = False
    droplet.meta["filtered"] = True
    return droplet
