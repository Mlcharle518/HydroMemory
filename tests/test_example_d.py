"""Acceptance test for PRD §12 Example D — sensitive memory gets frozen.

Drives :func:`hydromemory.examples.example_d.run` against a fully-wired engine on
a temp SQLite store (stub intelligence backend) and asserts the §12 Example D
end-state: phase=ice, reservoir=glacier, transformation disabled without
consent/thaw, permitted with consent+thaw, and recall gated by a safe context.
"""
from __future__ import annotations

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.examples.example_d import run


def test_example_d_sensitive_memory_frozen(tmp_db_path: str) -> None:
    engine = build_engine(
        HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    )
    try:
        facts = run(engine)
    finally:
        engine.close()

    # --- Capture: the sensitive memory was absorbed (LIQUID entry). ----------
    assert facts["absorbed_stored"] is True
    assert facts["absorbed_phase"] == "liquid"

    # --- Frozen end-state: phase=ice, reservoir=glacier. ---------------------
    assert facts["phase"] == "ice"
    assert facts["reservoir"] == "glacier"
    assert facts["is_ice"] is True
    assert facts["is_glacier"] is True

    # --- Transformation disabled without user consent/thaw. ------------------
    without = facts["transform_without_consent"]
    assert without["allowed"] is False
    # The denial carries the thaw/consent obligations (restricted access).
    assert "requires_thaw" in without["obligations"]
    assert "requires_consent" in without["obligations"]
    assert without["denial_reason"]

    # --- Transformation permitted with explicit consent + thaw. --------------
    with_consent = facts["transform_with_consent"]
    assert with_consent["allowed"] is True

    # --- Recall only in safe, relevant contexts. -----------------------------
    # A frozen glacier droplet sits behind a high recall threshold and does not
    # surface through the ordinary recall path.
    assert facts["recalled_in_unsafe_context"] is False
    assert facts["recalled_in_safe_context"] is False

    # MELT (reactivation) is the operation gated on a safe context: blocked
    # without one (stays ICE), thaws ICE->LIQUID with one.
    assert facts["melt_blocked_phase"] == "ice"
    assert facts["melt_blocked_reason"]
    assert facts["melted_phase"] == "liquid"
    assert facts["melted_reservoir"] == "working_stream"
