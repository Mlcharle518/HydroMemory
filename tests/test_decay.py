"""Salience-only time decay + aged selection (ADR-0032).

The centerpiece is the invariant: decay fades *salience* (pressure/fluidity/
temperature) but never *truth* (purity/integrity/confidence). That is precisely
what keeps a rare-but-true memory (quiet, high purity, recallable on a strong
pull) distinct from a stale-false one (the separate contamination path).
"""
from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

from hydromemory.forgetting import decay, select_aged
from hydromemory.platform.runtime import MeshEngine
from hydromemory.schema import Droplet, Phase, State


class _Repo:
    """Minimal duck-typed repo: ``select_aged`` only calls ``query()``."""

    def __init__(self, droplets: list[Droplet]) -> None:
        self._droplets = droplets

    def query(self, **kwargs: object) -> list[Droplet]:
        return list(self._droplets)


# --- decay: the core invariant ----------------------------------------------
def test_decay_fades_salience_but_never_truth():
    d = Droplet(
        id="x",
        state=State(
            pressure=0.8, fluidity=0.7, temperature=0.6,  # salience -> decays
            purity=0.9, integrity=0.85, confidence=0.95,  # truth -> untouched
            gravity=0.5, depth=0.3,                        # non-salience -> untouched
        ),
    )
    truth_before = (d.state.purity, d.state.integrity, d.state.confidence, d.state.gravity, d.state.depth)
    decay(d, idle_cycles=1)
    assert d.state.pressure < 0.8 and d.state.fluidity < 0.7 and d.state.temperature < 0.6
    assert (d.state.purity, d.state.integrity, d.state.confidence, d.state.gravity, d.state.depth) == truth_before
    assert d.meta["decayed"] is True


def test_decay_zero_cycles_is_noop():
    d = Droplet(id="x", state=State(pressure=0.8))
    decay(d, idle_cycles=0)
    assert d.state.pressure == 0.8
    assert "decayed" not in d.meta


def test_decay_to_floor_suggests_drain_never_deletes():
    d = Droplet(id="x", state=State(pressure=0.1, fluidity=0.1, temperature=0.1, purity=0.9))
    decay(d, idle_cycles=20)  # heavy fade -> well below the drain floor
    assert d.state.purity == 0.9          # truth intact
    assert d.phase is Phase.LIQUID        # decay never deletes / never contaminates
    assert d.meta["decay_suggestion"] == "drain"


def test_decay_mild_floor_suggests_sediment():
    d = Droplet(id="x", state=State(pressure=0.05, fluidity=0.03, purity=0.9))
    decay(d, idle_cycles=1)  # lands in (drain_floor, sediment_floor]
    assert d.meta["decay_suggestion"] == "sediment"


# --- select_aged ------------------------------------------------------------
def _now() -> datetime:
    return datetime(2026, 5, 25, tzinfo=UTC)


def test_select_aged_picks_unverified_and_stale():
    fresh = Droplet(id="fresh")
    fresh.cycle.last_verified = _now() - timedelta(days=1)
    stale = Droplet(id="stale")
    stale.cycle.last_verified = _now() - timedelta(days=30)
    never = Droplet(id="never")  # last_verified is None
    aged = select_aged(_Repo([fresh, stale, never]), now=_now(), max_age=timedelta(days=7))
    assert {d.id for d in aged} == {"stale", "never"}


def test_select_aged_can_exclude_unverified():
    never = Droplet(id="never")
    stale = Droplet(id="stale")
    stale.cycle.last_verified = _now() - timedelta(days=30)
    aged = select_aged(
        _Repo([never, stale]), now=_now(), max_age=timedelta(days=7), include_unverified=False
    )
    assert {d.id for d in aged} == {"stale"}


def test_select_aged_respects_limit():
    repo = _Repo([Droplet(id=f"n{i}") for i in range(10)])  # all never-verified
    assert len(select_aged(repo, now=_now(), limit=3)) == 3


# --- MeshEngine wiring -------------------------------------------------------
def test_meshengine_aged_droplets_real_query_with_repo():
    repo = _Repo([Droplet(id="never1"), Droplet(id="never2")])
    eng = MeshEngine(types.SimpleNamespace(), repo=repo)
    assert {d.id for d in eng.aged_droplets()} == {"never1", "never2"}


def test_meshengine_aged_droplets_passthrough_without_repo():
    eng = MeshEngine(types.SimpleNamespace())
    assert eng.aged_droplets() == []  # no repo -> empty passthrough (unchanged)
    d = Droplet(id="x")
    assert eng.aged_droplets({"droplets": [d]}) == [d]  # explicit droplets still win


def test_meshengine_decay_returns_distinct_copy():
    eng = MeshEngine(types.SimpleNamespace())
    d = Droplet(id="x", state=State(pressure=0.8, purity=0.9))
    out = eng.decay(d, idle_cycles=1)
    assert out is not d                 # distinct instance for the mesh no-op guard
    assert out.state.pressure < 0.8
    assert out.state.purity == 0.9
    assert d.state.pressure == 0.8      # original untouched (copy-return)
