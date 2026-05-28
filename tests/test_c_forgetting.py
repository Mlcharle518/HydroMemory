"""Track C forgetting-model tests (PRD §11): each of the seven verbs asserts its
exact phase / reservoir / state / meta deltas.
"""
from __future__ import annotations

from hydromemory.forgetting import (
    compost,
    delete,
    dissolve,
    drain,
    evaporate,
    seal,
    sediment,
)
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Links, Phase, Retention, State


def make_droplet(**state_kw) -> Droplet:
    fields = dict(
        temperature=0.8,
        pressure=0.6,
        fluidity=0.9,
        depth=0.3,
        integrity=0.9,
        purity=0.5,
    )
    fields.update(state_kw)
    return Droplet(
        id="mem_test",
        content="I was dismissed during a meeting.",
        phase=Phase.LIQUID,
        reservoir=Reservoir.WORKING_STREAM,
        state=State(**fields),
        links=Links(),
    )


def test_evaporate_keeps_pattern_drops_detail():
    d = make_droplet()
    before_fluidity = d.state.fluidity
    out = evaporate(d)
    assert out is d  # mutates in place, returns same droplet
    assert out.phase is Phase.VAPOR
    assert out.state.fluidity < before_fluidity
    assert out.state.depth < 0.3
    assert out.state.temperature < 0.8
    # the gist (pre-evaporation content) is preserved.
    assert out.meta["gist"] == "I was dismissed during a meeting."
    assert out.meta["evaporated"] is True


def test_evaporate_does_not_push_nonliquid_backwards():
    d = make_droplet()
    d.phase = Phase.CLOUD
    out = evaporate(d)
    assert out.phase is Phase.CLOUD  # stays, not forced to vapor


def test_drain_removes_active_influence():
    d = make_droplet()
    out = drain(d)
    assert out is d
    assert out.state.pressure == 0.0
    assert out.state.fluidity == 0.0
    assert out.meta["active"] is False
    assert out.meta["drained"] is True
    # reservoir and content untouched.
    assert out.reservoir is Reservoir.WORKING_STREAM
    assert out.content == "I was dismissed during a meeting."


def test_sediment_sinks_to_archive():
    d = make_droplet()
    out = sediment(d)
    assert out is d
    assert out.reservoir is Reservoir.GROUNDWATER
    assert out.phase is Phase.GROUNDWATER
    assert out.permissions.retention is Retention.ARCHIVED
    assert out.state.depth > 0.3  # sank deeper
    assert out.state.fluidity < 0.9  # slow to recall
    assert out.meta["sedimented"] is True


def test_dissolve_merges_into_cluster():
    d = make_droplet()
    before_integrity = d.state.integrity
    out = dissolve(d, into_id="cluster_42")
    assert out is d
    assert out.meta["merged_into"] == "cluster_42"
    assert out.meta["dissolved"] is True
    assert "cluster_42" in out.links.derived_from
    assert out.state.integrity < before_integrity  # surrendered identity


def test_dissolve_does_not_duplicate_parent_link():
    d = make_droplet()
    d.links.derived_from.append("cluster_42")
    out = dissolve(d, into_id="cluster_42")
    assert out.links.derived_from.count("cluster_42") == 1


def test_seal_freezes_inaccessible():
    d = make_droplet()
    out = seal(d)
    assert out is d
    assert out.reservoir is Reservoir.GLACIER
    assert out.phase is Phase.ICE
    assert out.state.fluidity == 0.0
    assert out.meta["sealed"] is True
    assert out.meta["accessible"] is False


def test_compost_becomes_lesson_discards_detail():
    d = make_droplet()
    before_depth = d.state.depth
    out = compost(d, lesson="Being ignored in public feels like erasure.")
    assert out is d
    assert out.content == "Being ignored in public feels like erasure."
    assert out.meta["original_detail_discarded"] is True
    assert out.meta["composted_from"] == "I was dismissed during a meeting."
    assert out.state.depth > before_depth  # a principle is deeper/settled
    assert out.state.purity >= 0.8


def test_delete_returns_none():
    d = make_droplet()
    assert delete(d) is None


def test_forgetting_state_floats_stay_in_unit_range():
    # Compose several verbs and confirm nothing leaves [0,1].
    d = make_droplet(temperature=1.0, depth=1.0)
    sediment(d)
    compost(d, lesson="lesson")
    for fld in ("temperature", "pressure", "gravity", "purity", "salinity",
                "depth", "fluidity", "integrity", "confidence"):
        v = getattr(d.state, fld)
        assert 0.0 <= v <= 1.0, f"{fld}={v} out of range"
