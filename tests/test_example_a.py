"""Acceptance test for PRD §12 Example A (meeting dismissal -> cloud recall).

Builds a real :class:`~hydromemory.engine.Engine` on a temp SQLite DB with the
deterministic stub intelligence backend, runs the example end-to-end, and asserts
the scenario's intended end-state:

* the original experience persists as a LIQUID droplet;
* it EVAPORATEs into a VAPOR pattern droplet linked ``derived_from`` the original;
* the related patterns CONDENSE into a CLOUD droplet themed "social invisibility";
* a recall / PRECIPITATE surfaces the invisibility/erasure pattern (non-empty list
  of :class:`~hydromemory.recall.RecallResult`).
"""
from __future__ import annotations

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.examples.example_a import run
from hydromemory.recall import RecallResult


def test_example_a_meeting_dismissal_to_cloud_recall(tmp_db_path: str) -> None:
    cfg = HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    engine = build_engine(cfg)
    try:
        facts = run(engine)

        # 1. The original experience is stored as a LIQUID droplet.
        assert facts["original_phase"] == "liquid"

        # 2. EVAPORATE produced a VAPOR pattern droplet derived_from the original.
        assert facts["primary_vapor_phase"] == "vapor"
        assert facts["original_id"] in facts["primary_vapor_derived_from"]
        assert facts["primary_vapor_id"] != facts["original_id"]

        # 3. CONDENSE produced a CLOUD droplet themed "social invisibility",
        #    derived from every vapor pattern in the cluster.
        assert facts["cloud_phase"] == "cloud"
        assert facts["cloud_theme"] == "social invisibility"
        assert len(facts["vapor_ids"]) >= 2
        assert set(facts["vapor_ids"]) == set(facts["cloud_members"])
        assert set(facts["vapor_ids"]) == set(facts["cloud_derived_from"])
        # The cloud and the original are distinct droplets in distinct phases.
        assert facts["cloud_id"] != facts["original_id"]

        # 4. The recall / PRECIPITATE surfaces the pattern: a non-empty list of
        #    RecallResult, at least one of which is a droplet from the
        #    invisibility/erasure pattern (a vapor or the cloud).
        results = facts["recall_results"]
        assert isinstance(results, list)
        assert facts["recall_count"] >= 1
        assert all(isinstance(r, RecallResult) for r in results)
        assert facts["recall_is_pattern_related"] is True
    finally:
        engine.close()
