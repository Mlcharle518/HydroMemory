"""Shared harness for the PRD §12 source-example scenarios.

Each example module (``example_a`` … ``example_f``) exposes
``run(engine) -> dict`` and is driven either by its acceptance test (which injects
an engine built on a temp DB fixture) or by ``hydromem run-example`` (which builds
a throwaway demo engine here).
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from hydromemory.config import HydroConfig
from hydromemory.engine import Engine, build_engine


def build_demo_engine() -> tuple[Engine, str]:
    """Build an Engine over a throwaway temp SQLite store. Returns (engine, tmpdir)."""
    tmpdir = tempfile.mkdtemp(prefix="hydro_demo_")
    cfg = HydroConfig(db_path=f"{tmpdir}/demo.db", vector_dim=64, intelligence_backend="stub")
    return build_engine(cfg), tmpdir


@contextmanager
def demo_engine() -> Iterator[Engine]:
    """Context manager yielding a demo Engine, cleaned up on exit."""
    engine, tmpdir = build_demo_engine()
    try:
        yield engine
    finally:
        engine.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def banner(title: str) -> None:
    print(f"\n=== {title} ===")
