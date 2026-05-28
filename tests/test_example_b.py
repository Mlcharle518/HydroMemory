"""Acceptance test for PRD §12 Example B — *Preference becomes principle*.

Asserts the intended end-state: a repeated structural-intelligence preference is
abstracted (EVAPORATE), clustered into a cognitive-style CLOUD (CONDENSE), and
sunk into identity-level **GROUNDWATER** (INFILTRATE) as a durable principle; a
recall in an architecture-help context returns **behavioural** guidance that
reflects the depth preference without quoting the memory.
"""
from __future__ import annotations

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.examples.example_b import PRINCIPLE_TEXT, run
from hydromemory.recall import RecallMode
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Phase


def test_example_b_preference_becomes_principle(tmp_db_path: str) -> None:
    engine = build_engine(
        HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    )
    try:
        facts = run(engine)

        # --- Repetition was established across multiple raw experiences. ----
        assert facts["raw_count"] >= 3
        assert facts["max_cycle_count"] >= 1
        assert facts["vapor_count"] == facts["raw_count"]

        # --- Condensation produced a cognitive-style cloud. -----------------
        assert facts["cloud_phase"] == Phase.CLOUD.value
        assert "systems thinking" in facts["cloud_theme"]

        # --- The principle settled into identity-level groundwater. ---------
        assert facts["reached_groundwater"] is True
        assert facts["principle_phase"] == Phase.GROUNDWATER.value
        assert facts["principle_reservoir"] == Reservoir.GROUNDWATER.value
        assert facts["principle_text"] == PRINCIPLE_TEXT
        # Infiltration deepened the droplet (depth/gravity rose).
        assert facts["principle_depth"] >= 0.3
        assert facts["principle_gravity"] >= 0.5
        # The principle traces back to the cognitive-style cloud.
        assert facts["principle_derived_from_cloud"] is True
        # The groundwater layer holds exactly this durable principle.
        assert facts["groundwater_count"] == 1

        # The repository confirms the droplet is queryable in GROUNDWATER and
        # holds the depth/architecture principle.
        groundwater = engine.repo.query(reservoir=Reservoir.GROUNDWATER)
        assert len(groundwater) == 1
        principle = groundwater[0]
        assert principle.phase is Phase.GROUNDWATER
        assert principle.id == facts["principle_id"]
        for keyword in ("architecture", "mechanisms", "frameworks"):
            assert keyword in principle.content

        # --- Recall in an architecture-help context yields depth guidance. --
        assert facts["principle_recalled"] is True
        assert facts["recall_is_behavioral"] is True
        assert facts["recall_mode"] == RecallMode.BEHAVIORAL.value
        assert facts["recall_score"] > 0.65  # clears the groundwater threshold
        # Behavioural recall adapts behaviour without surfacing the memory text.
        assert facts["recall_quotes_memory"] is False
        guidance = facts["recall_guidance"].lower()
        assert "architecture" in guidance or "frameworks" in guidance
    finally:
        engine.close()
