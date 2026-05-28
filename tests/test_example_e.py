"""Acceptance test for PRD §12 Example E — polluted memory becomes filtered.

Asserts the load-bearing end-state transition described in §12 Example E
(contamination §10.1, filtering §11):

* before filtering: phase == ``polluted`` and the droplet is NOT usable for
  generation;
* after filtering: phase == ``filtered``, purity is RAISED, the droplet leaves
  the contaminated reservoir, and it is usable for generation again.

The engine's ``filter_droplet`` repairs state but does not rewrite the droplet
content to the §12 reframed sentence (see the GAP note in ``example_e``); the
illustrative reframe is verified via ``reframed_content`` instead.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.examples.example_e import (
    FILTERED_CONTENT,
    POLLUTED_CONTENT,
    run,
)
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Phase


def test_example_e_polluted_becomes_filtered(config: HydroConfig) -> None:
    engine = build_engine(config)
    try:
        facts: dict[str, Any] = run(engine)
    finally:
        engine.close()

    before = facts["before"]
    after = facts["after"]

    # --- before filtering: polluted + unusable -----------------------------
    assert before["phase"] == Phase.POLLUTED.value
    assert before["reservoir"] == Reservoir.CONTAMINATED.value
    assert before["usable_for_generation"] is False
    assert before["requires_filtering"] is True

    # --- after filtering: filtered + usable, purity raised -----------------
    assert after["phase"] == Phase.FILTERED.value
    assert after["reservoir"] != Reservoir.CONTAMINATED.value
    assert after["usable_for_generation"] is True
    assert after["requires_filtering"] is False

    # purity strictly increased from the polluted state to the filtered state.
    assert after["purity"] > before["purity"]
    assert facts["purity_raised"] is True

    # --- the §12 illustrative reframe is the softer, usable interpretation --
    assert facts["polluted_content"] == POLLUTED_CONTENT
    assert facts["reframed_content"] == FILTERED_CONTENT
    assert facts["reason"]  # the pollution reason is preserved/audited

    # GAP vs spec: the engine repairs state (phase/purity/usability) but does
    # not rewrite content to the §12 sentence; assert the closest faithful
    # behavior — content is unchanged by the engine's filter.
    assert facts["content_rewritten_by_engine"] is False


def test_example_e_runs_end_to_end(tmp_path: Path) -> None:
    """The scenario runs end to end and returns a stable droplet id + reframe."""
    cfg = HydroConfig(
        db_path=str(tmp_path / "demo.db"), vector_dim=64, intelligence_backend="stub"
    )
    engine = build_engine(cfg)
    try:
        facts = run(engine)
    finally:
        engine.close()

    assert facts["before"]["phase"] == Phase.POLLUTED.value
    assert facts["after"]["phase"] == Phase.FILTERED.value
    assert facts["droplet_id"]
    assert facts["reframed_content"] == FILTERED_CONTENT
