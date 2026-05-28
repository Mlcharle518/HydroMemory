"""Opt-in autonomic consolidation on the mesh (ADR-0031, the bus auto-trigger).

Over a REAL vault + bus + mesh: publishing an ABSORBED event for a droplet whose
linked constellation is dense enough makes the mesh gather→cluster→distill it into
a CLOUD principle — without any manual call — and announce DISTILLED. Sparse
neighborhoods and the default (consolidate=False) produce nothing.

The principle lands in CLOUD (ADR-0036) so ordinary approved agents can reuse it at
recall. The mesh still writes it through a derived user-proxy view (see
`Mesh._principal_vault`) — the owner is the writer of consolidated memory — though
that proxy is no longer *required* to clear governance now that principles are not
SACRED.
"""
from __future__ import annotations

from hydromemory.bus.bus import EventBus
from hydromemory.bus.events import EventType, MemoryEvent
from hydromemory.config import HydroConfig
from hydromemory.governance import check_access
from hydromemory.intelligence import build_intelligence
from hydromemory.platform.runtime import build_mesh
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase


def _make(tmp_path, *, consolidate: bool):
    cfg = HydroConfig(
        db_path=str(tmp_path / "consolidate.db"),
        vector_dim=64,
        intelligence_backend="stub",
        vault_key="consolidate-secret",
    )
    intel = build_intelligence(cfg)
    from hydromemory.vault import open_vault_store

    vault = open_vault_store(cfg)  # default identity = user-proxy, cross-app
    # bus max_depth=2 so the depth-1 follow-on (DISTILLED) is delivered to the
    # observer; mesh max_depth=1 keeps each reaction to a single bounded hop.
    bus = EventBus(repo=vault, check_access=check_access, max_depth=2)
    seen: list[tuple[str, str | None, int | None]] = []
    bus.subscribe(topics=None, handler=lambda e: seen.append((e.type, e.droplet_id, e.payload.get("_depth"))))
    mesh = build_mesh(vault, bus, intel, audit=vault.audit, max_depth=1, consolidate=consolidate)
    mesh.attach()
    return cfg, vault, bus, mesh, seen


def _clean(did: str, **links: list[str]) -> Droplet:
    d = Droplet.from_dict(
        {
            "id": did,
            "content": f"note about {did}",
            "reservoir": "working_stream",
            "state": {"purity": 0.9, "confidence": 0.9, "gravity": 0.4, "integrity": 0.6, "fluidity": 0.6},
        }
    )
    for kind, ids in links.items():
        setattr(d.links, kind, list(ids))
    return d


def _distilled_ids(seen: list[tuple[str, str | None, int | None]]) -> list[str | None]:
    return [did for (t, did, _depth) in seen if t == EventType.DISTILLED.value]


def test_dense_constellation_auto_distills_principle(tmp_path):
    _cfg, vault, bus, _mesh, seen = _make(tmp_path, consolidate=True)
    # d1 -> d2 -> d3 form one linked constellation.
    vault.upsert(_clean("d1", associations=["d2"]))
    vault.upsert(_clean("d2", associations=["d3"]))
    vault.upsert(_clean("d3"))

    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="d1", payload={"_depth": 0}))

    distilled = _distilled_ids(seen)
    assert distilled, "expected a DISTILLED principle to be emitted autonomically"
    principle = vault.get(distilled[0])
    assert principle is not None
    assert principle.reservoir is Reservoir.CLOUD  # ADR-0036 (was SACRED)
    assert principle.phase is Phase.GROUNDWATER
    assert set(principle.meta["distilled_from"]) >= {"d1", "d2", "d3"}
    # Cascade stays bounded: nothing is delivered beyond depth 1.
    assert all((depth or 0) < 2 for (_t, _did, depth) in seen)


def test_sparse_droplet_is_not_consolidated(tmp_path):
    _cfg, vault, bus, _mesh, seen = _make(tmp_path, consolidate=True)
    vault.upsert(_clean("solo"))  # no links -> constellation of size 1

    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="solo", payload={"_depth": 0}))

    assert _distilled_ids(seen) == []  # density gate: too sparse


def test_consolidation_off_by_default(tmp_path):
    _cfg, vault, bus, _mesh, seen = _make(tmp_path, consolidate=False)
    vault.upsert(_clean("d1", associations=["d2"]))
    vault.upsert(_clean("d2"))

    bus.publish(MemoryEvent(type=EventType.ABSORBED.value, droplet_id="d1", payload={"_depth": 0}))

    assert _distilled_ids(seen) == []  # consolidate=False -> no auto-distill
