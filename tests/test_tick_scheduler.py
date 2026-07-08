"""Tests for Engine.tick + TickScheduler and the cfg.default_reservoir plumbing.

The tick math is deterministic given ``now`` (an explicit argument), so the
engine-level tests don't sleep — they pass synthetic timestamps. The scheduler
test exercises the thread lifecycle with a very short interval and an
``on_tick`` callback so we can observe the loop running without flakiness.
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.reservoirs import Reservoir
from hydromemory.scheduler import TickScheduler

# An approved-trust agent — needed to write into surface/groundwater etc.;
# the bare absorb() call uses a session-trust default that only working_stream allows.
APPROVED_AGENT = AgentIdentity(name="test-agent", trust_level=TrustLevel.APPROVED)


def _build(tmp_path, **overrides):
    cfg = HydroConfig(db_path=str(tmp_path / "tick.db"), **overrides)
    return build_engine(cfg)


# ---------------------------------------------------------------------------
# default_reservoir plumbing
# ---------------------------------------------------------------------------


def test_absorb_uses_configured_default_reservoir(tmp_path):
    # "surface" is the cleanest non-default to assert against — `working_stream`
    # is the prior hard-coded default, and `groundwater`/`glacier` need
    # high-trust agents the absorb pipeline won't grant a vanilla call.
    engine = _build(tmp_path, default_reservoir="surface")
    try:
        decision = engine.absorb(
            "a mundane statement", source="conversation", agent=APPROVED_AGENT
        )
        assert decision["stored"]
        droplet = engine.repo.get(decision["droplet_id"])
        assert droplet is not None
        # Low-sensitivity content with no sacred/contaminated triggers should
        # land in the configured default, not the hard-coded WORKING_STREAM.
        assert droplet.reservoir is Reservoir.SURFACE
    finally:
        engine.close()


def test_absorb_default_reservoir_unchanged_for_default_config(tmp_path):
    engine = _build(tmp_path)  # default_reservoir = "working_stream"
    try:
        decision = engine.absorb("another mundane statement", source="conversation")
        droplet = engine.repo.get(decision["droplet_id"])
        assert droplet.reservoir is Reservoir.WORKING_STREAM
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Engine.tick
# ---------------------------------------------------------------------------


def _seed_droplet(engine, *, did="seed", pressure=0.8, fluidity=0.6, temperature=0.5):
    """Insert a droplet directly with non-zero salience so the decay assertion has
    something to fade — absorb()'s classifier seeds those dims at 0 for plain text."""
    from hydromemory.schema import Droplet

    droplet = Droplet.from_dict(
        {
            "id": did,
            "content": "seeded for decay",
            "state": {"pressure": pressure, "fluidity": fluidity, "temperature": temperature},
        }
    )
    engine.repo.upsert(droplet)
    return engine.repo.get(did)


def test_tick_decays_idle_droplet(tmp_path):
    engine = _build(tmp_path, cycle_tick_seconds=1.0)
    try:
        seeded = _seed_droplet(engine)
        original_pressure = seeded.state.pressure
        # Synthetic "5 seconds later" — 5 ticks of decay at salience_factor=0.85.
        future = (seeded.created_at or datetime.now(UTC)) + timedelta(seconds=5)
        stats = engine.tick(now=future)
        assert stats["seen"] == 1
        assert stats["decayed"] == 1
        assert stats["cycles_total"] == 5
        decayed = engine.repo.get(seeded.id)
        assert decayed.state.pressure < original_pressure
        assert decayed.meta["decayed"] is True
        assert "last_decayed_at" in decayed.meta
    finally:
        engine.close()


def test_tick_is_idempotent_within_same_interval(tmp_path):
    engine = _build(tmp_path, cycle_tick_seconds=1.0)
    try:
        _seed_droplet(engine)
        now = datetime.now(UTC) + timedelta(seconds=10)
        first = engine.tick(now=now)
        second = engine.tick(now=now)
        assert first["decayed"] == 1
        # A second tick at the same `now` has no elapsed time since last_decayed_at,
        # so the floor of (0 / interval) is 0 -> no further decay.
        assert second["decayed"] == 0
    finally:
        engine.close()


def test_tick_skips_recently_recalled(tmp_path):
    engine = _build(tmp_path, cycle_tick_seconds=10.0)  # 10s ticks => 1s elapsed is 0 cycles
    try:
        _seed_droplet(engine)
        now = datetime.now(UTC) + timedelta(seconds=1)
        stats = engine.tick(now=now)
        assert stats["seen"] == 1
        assert stats["decayed"] == 0
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# TickScheduler
# ---------------------------------------------------------------------------


def test_scheduler_runs_at_least_one_tick_and_stops_cleanly(tmp_path):
    cfg = HydroConfig(db_path=str(tmp_path / "sch.db"), cycle_tick_seconds=0.05)
    ticked = threading.Event()

    def on_tick(stats):
        ticked.set()

    with TickScheduler(cfg, interval_seconds=0.05, on_tick=on_tick) as scheduler:
        assert ticked.wait(timeout=2.0), (
            f"scheduler did not call on_tick within 2s "
            f"(last_error={scheduler.last_error!r})"
        )
        assert scheduler.last_stats is not None
    # Context-manager exit calls stop(); thread should be cleaned up.
    assert scheduler._thread is None


def test_scheduler_idempotent_double_start(tmp_path):
    cfg = HydroConfig(db_path=str(tmp_path / "sch.db"), cycle_tick_seconds=0.05)
    scheduler = TickScheduler(cfg, interval_seconds=0.05)
    scheduler.start()
    first_thread = scheduler._thread
    scheduler.start()  # should be a no-op while already running
    assert scheduler._thread is first_thread
    scheduler.stop()


def test_scheduler_records_tick_errors_without_dying(tmp_path, monkeypatch):
    cfg = HydroConfig(db_path=str(tmp_path / "sch.db"), cycle_tick_seconds=0.02)
    calls = {"n": 0}

    # Monkeypatch Engine.tick globally — the scheduler builds its own engine
    # in-thread, so per-instance patching wouldn't reach it.
    from hydromemory.engine import Engine

    original_tick = Engine.tick

    def flaky_tick(self, now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated failure")
        return original_tick(self, now=now)

    monkeypatch.setattr(Engine, "tick", flaky_tick)
    with TickScheduler(cfg, interval_seconds=0.02):
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if calls["n"] >= 2:
                break
            time.sleep(0.02)
    assert calls["n"] >= 2, "scheduler did not recover from the simulated failure"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_tick_oneshot(tmp_path, capsys):
    import json as _json

    from hydromemory.cli import main

    db = str(tmp_path / "cli-tick.db")
    rc = main(["--db", db, "absorb", "--content", "for the tick cli", "--source", "conversation"])
    assert rc == 0
    capsys.readouterr()  # drain
    rc = main(["--db", db, "tick"])
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["seen"] == 1
