"""Acceptance test for PRD §12 Example F — conflict resolution.

Builds a real :class:`Engine` over a temp SQLite store (stub intelligence) and
asserts the reconciliation end-state: the conflict is detected (not overwritten),
the interpretation is context-dependent, and the reconciled memory is a FILTERED
droplet at the spec's 0.92 purity.
"""
from __future__ import annotations

from typing import Any

import pytest

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.examples.example_f import run
from hydromemory.schema import Phase


@pytest.fixture
def engine(tmp_db_path: str):
    cfg = HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    try:
        yield eng
    finally:
        eng.close()


def test_example_f_conflict_resolution(
    engine: Any, spec_droplet_blobs: dict[str, dict[str, Any]]
) -> None:
    payload = run(engine)

    # --- Top-level resolution shape (PRD §12 Example F) ---------------------
    assert payload["conflict"] is True
    assert isinstance(payload["interpretation"], str)

    # Substantive engine-state check (not the runner's hardcoded constant): the
    # reconciled FILTERED droplet must link back to BOTH source droplets via its
    # derived_from *and* contradictions edges, and both originals must remain
    # independently queryable -- i.e. the conflict was reconciled, not overwritten.
    filtered = engine.repo.query(phase=Phase.FILTERED)
    assert len(filtered) == 1
    reconciled = filtered[0]

    originals = [d for d in engine.repo.query(phase=Phase.LIQUID) if d.id != reconciled.id]
    source_ids = {d.id for d in originals}
    assert len(source_ids) == 2  # the two conflicting memories both survive

    assert set(reconciled.links.derived_from) == source_ids
    assert set(reconciled.links.contradictions) == source_ids
    # Each original is still retrievable on its own (history preserved).
    for sid in source_ids:
        assert engine.repo.get(sid) is not None

    # --- Reconciled (updated) memory ---------------------------------------
    updated = payload["updated_memory"]
    assert updated["phase"] == "filtered"

    purity = updated["purity"]
    assert isinstance(purity, float)
    # Spec target is 0.92; the FILTER verb raises purity to exactly that floor.
    assert purity >= 0.9
    assert purity == pytest.approx(0.92, abs=1e-6)

    # The reconciled content expresses a context-dependent preference, matching
    # the spec's §12 Example F updated_memory blob.
    spec_updated = spec_droplet_blobs["example_f"]
    assert updated["content"] == spec_updated["content"]
    assert updated["phase"] == spec_updated["phase"]
    assert updated["purity"] == pytest.approx(spec_updated["purity"], abs=1e-6)


def test_example_f_persists_filtered_droplet(engine: Any) -> None:
    """The reconciled FILTERED droplet is actually persisted and recallable."""
    run(engine)

    filtered = engine.repo.query(phase=Phase.FILTERED)
    assert len(filtered) == 1
    droplet = filtered[0]
    assert droplet.state.purity == pytest.approx(0.92, abs=1e-6)
    # The conflict was recorded, not overwritten: the reconciled droplet links
    # back to the two source memories it derives from *and* contradicts, and
    # both originals remain queryable in their own right.
    source_ids = {d.id for d in engine.repo.query(phase=Phase.LIQUID) if d.id != droplet.id}
    assert len(source_ids) == 2
    assert set(droplet.links.derived_from) == source_ids
    assert set(droplet.links.contradictions) == source_ids
    for sid in source_ids:
        assert engine.repo.get(sid) is not None
