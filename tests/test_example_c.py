"""Acceptance test for PRD §12 Example C (the §16 over-memory-reduction guard).

A single absorb of "User asked about running shoes." — with no repetition and no
INFILTRATE — must stay a transient, surface-level memory and must NOT infiltrate
into identity-level (groundwater / sacred) storage, nor spawn a "User is a
runner." identity droplet.
"""
from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.examples.example_c import run


@pytest.fixture()
def tmp_db_path() -> Iterator[str]:
    with tempfile.TemporaryDirectory(prefix="hydro_example_c_") as tmpdir:
        yield str(Path(tmpdir) / "example_c.db")


def test_example_c_temporary_task_does_not_become_identity(tmp_db_path: str) -> None:
    config = HydroConfig(
        db_path=tmp_db_path,
        vector_dim=64,
        intelligence_backend="stub",
    )
    engine = build_engine(config)
    try:
        facts = run(engine)

        # The droplet was captured and stored.
        assert facts["stored"] is True
        assert facts["content"] == "User asked about running shoes."

        # Phase = LIQUID: fresh experience, no transition fired.
        assert facts["phase"] == "liquid"

        # Reservoir is a shallow / fast layer (working_stream or surface), NOT an
        # identity-level reservoir.
        assert facts["reservoir"] in {"working_stream", "surface"}
        assert facts["is_shallow_reservoir"] is True
        assert facts["reservoir"] not in {"groundwater", "sacred"}

        # Shallow depth: a transient fact has not sunk into deep storage.
        assert facts["depth"] < 0.3

        # No cycling: this is a one-off, no repetition.
        assert facts["cycle_count"] == 0

        # The guardrail: no infiltration into identity memory.
        assert facts["infiltrated_to_identity"] is False

        # Contrast: no "User is a runner." identity droplet was ever created;
        # only the single transient running-shoes droplet exists.
        assert facts["identity_droplet_created"] is False
        assert facts["stored_droplet_count"] == 1
    finally:
        engine.close()
