"""Phase 2 Done gate: end-to-end through the fully-wired real stack.

Proves absorb -> classify -> phase -> reservoir -> store -> (persist across a
reopen) -> recall + HQL, using the real SQLite store, the stub intelligence
backend, real governance, verbs, and HQL — no mocks.
"""
from __future__ import annotations

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.recall import RecallResult
from hydromemory.schema import Droplet


def _cfg(tmp_db_path: str) -> HydroConfig:
    return HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")


def test_absorb_store_recall_survives_restart(tmp_db_path):
    engine = build_engine(_cfg(tmp_db_path))
    try:
        decision = engine.absorb(
            "User prefers architectural systems thinking over shallow summaries.",
            source="conversation",
            context={"topic": "AI memory systems", "session_type": "design"},
        )
        assert decision["stored"] is True
        assert decision["droplet_id"]
        assert decision["phase"] == "liquid"
        target_id = decision["droplet_id"]
        engine.absorb("User asked about buying running shoes.", context={"topic": "shopping"})
    finally:
        engine.close()

    # Reopen a brand-new Engine on the same DB file -> state persisted.
    engine2 = build_engine(_cfg(tmp_db_path))
    try:
        assert len(engine2.repo.all_ids()) == 2
        restored = engine2.repo.get(target_id)
        assert isinstance(restored, Droplet)
        assert "architectural" in restored.content

        results = engine2.recall("architecture systems thinking")
        assert results, "expected at least one recall hit through the real stack"
        assert all(isinstance(r, RecallResult) for r in results)
        assert results[0].score > 0
    finally:
        engine2.close()


def test_hql_get_and_precipitate_through_engine(tmp_db_path):
    engine = build_engine(_cfg(tmp_db_path))
    try:
        engine.absorb(
            "User values deep architecture, mechanisms, and executable frameworks.",
            context={"topic": "AI memory"},
        )
        rows = engine.hql('GET memories WHERE phase="liquid"')
        assert isinstance(rows, list)
        assert rows and all(isinstance(d, Droplet) for d in rows)

        out = engine.hql(
            'PRECIPITATE cloud WHERE theme="architecture" AND trigger="system architecture request"'
        )
        assert isinstance(out, list)  # recall results (possibly empty), not the raw op
    finally:
        engine.close()
