"""Runnable PRD §12 source examples (A–F).

Each ``example_<x>`` module exposes ``run(engine) -> dict`` returning the
scenario's end-state facts. ``run_example`` dispatches by letter, building a
throwaway demo engine when one isn't supplied.
"""
from __future__ import annotations

import importlib
from typing import Any

from hydromemory.engine import Engine
from hydromemory.examples._harness import demo_engine

EXAMPLE_NAMES = ["A", "B", "C", "D", "E", "F"]


def run_example(name: str, engine: Engine | None = None) -> dict[str, Any]:
    """Run example ``name`` (A–F). Uses ``engine`` if given, else a demo engine."""
    key = name.strip().upper()
    if key not in EXAMPLE_NAMES:
        raise ValueError(f"unknown example {name!r}; choose from {EXAMPLE_NAMES}")
    module = importlib.import_module(f"hydromemory.examples.example_{key.lower()}")
    if engine is not None:
        return module.run(engine)
    with demo_engine() as eng:
        return module.run(eng)


__all__ = ["run_example", "EXAMPLE_NAMES"]
