"""L3 — Agentic Memory Mesh over a REAL bus + REAL vault (v2 Phase B2).

Scenario: a vault-backed :class:`~hydromemory.bus.bus.EventBus` plus a
:class:`~hydromemory.platform.mesh.Mesh` (built by
:func:`hydromemory.platform.runtime.build_mesh`). Publishing an ``ABSORBED``
event for a droplet the stub contamination detector flags (low confidence)
makes the mesh's filtration reaction assess + route it to the contaminated pool
*without any manual agent call* — and the follow-on event is bounded to a single
cascade hop (no storm).

Two wiring facts the scenario depends on (both surfaced while integrating B1):

* **The mesh writes through a *filtration*-identity vault.** The mesh persists
  the routed droplet via ``vault.upsert``, which gates on the *vault's own*
  identity. Routing sends a droplet to the ``contaminated`` reservoir, which is
  ``filtration_agent_only`` — so a user-proxy vault's upsert of it would be
  denied and the route silently lost. The vault the mesh writes through is
  therefore opened under a HIGH_TRUST ``is_filtration`` identity. (READ of the
  working_stream/contaminated droplets under that identity is also allowed, so
  the bus permission gate delivers to the filtration subscriber.)

* **The mesh's no-op guard needs a distinct returned instance.**
  ``Mesh._unchanged`` treats ``before is after`` as a no-op. The
  ``contamination`` helpers mutate-and-return the *same* object, so
  :class:`~hydromemory.platform.runtime.MeshEngine` returns a *copy* — the mesh
  then compares ``to_dict`` and sees the real change. (Asserted indirectly: the
  route actually persists.)
"""
from __future__ import annotations

import pytest

from hydromemory.bus.bus import EventBus
from hydromemory.bus.events import EventType, MemoryEvent
from hydromemory.config import HydroConfig
from hydromemory.governance import AgentIdentity, TrustLevel, check_access
from hydromemory.intelligence import build_intelligence
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase
from hydromemory.vault import open_vault_store
from hydromemory.vault.scope import AppScope


def _filtration_vault(cfg: HydroConfig):
    """A cross-app vault the mesh writes through, authorized as filtration."""
    ident = AgentIdentity(
        name="filtration", trust_level=TrustLevel.HIGH_TRUST, is_filtration=True
    )
    return open_vault_store(cfg, identity=ident, scope=AppScope(cross_app=True))


@pytest.fixture
def harness(tmp_path):
    cfg = HydroConfig(
        db_path=str(tmp_path / "l3.db"),
        vector_dim=64,
        intelligence_backend="stub",
        vault_key="l3-secret",
    )
    intel = build_intelligence(cfg)
    vault = _filtration_vault(cfg)
    # Bus re-entrancy budget vs mesh reaction-hop budget are distinct guards.
    # After the L4 fix the bus guard is ``>=``, so ``max_depth`` counts *delivered*
    # nesting levels: the single mesh hop is a top-level ABSORBED dispatch (level
    # 0) whose handler emits ONE nested follow-on (level 1), so the bus needs
    # ``max_depth=2`` for that follow-on to be delivered/observed. The mesh keeps
    # ``max_depth=1`` (one reaction hop): its ``payload["_depth"]`` guard stops the
    # depth-1 follow-on from re-reacting, which is what bounds the cascade.
    bus = EventBus(repo=vault, check_access=check_access, max_depth=2)

    # Observe every event (including the mesh's follow-ons) for cascade asserts.
    seen: list[tuple[str, int | None]] = []
    bus.subscribe(topics=None, handler=lambda e: seen.append((e.type, e.payload.get("_depth"))))

    from hydromemory.platform.runtime import build_mesh

    mesh = build_mesh(vault, bus, intel, audit=vault.audit, max_depth=1)
    mesh.attach()
    try:
        yield {"cfg": cfg, "vault": vault, "bus": bus, "mesh": mesh, "seen": seen}
    finally:
        vault.close()


def _flagged(did: str = "m1") -> Droplet:
    """A low-confidence working_stream droplet the detector flags as polluted."""
    return Droplet.from_dict(
        {
            "id": did,
            "content": "maybe the meeting is tuesday",
            "reservoir": "working_stream",
            "state": {"purity": 0.5, "confidence": 0.1},
        }
    )


def test_l3_absorbed_event_routes_contamination_without_manual_call(harness):
    vault = harness["vault"]
    bus = harness["bus"]

    vault.upsert(_flagged("m1"))
    assert vault.get("m1").phase is Phase.LIQUID
    assert vault.get("m1").reservoir is Reservoir.WORKING_STREAM

    # Publish ABSORBED — the mesh reaction (filtration.assess_and_route) fires.
    delivered = bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, actor="app", droplet_id="m1", payload={"_depth": 0})
    )
    assert delivered >= 1

    # The droplet is now routed to the contaminated pool, no manual agent call.
    after = vault.get("m1")
    assert after.phase is Phase.POLLUTED
    assert after.reservoir is Reservoir.CONTAMINATED
    assert after.meta.get("usable_for_generation") is False


def test_l3_single_cascade_hop_no_storm(harness):
    vault = harness["vault"]
    bus = harness["bus"]
    seen = harness["seen"]

    vault.upsert(_flagged("m1"))
    bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="m1", payload={"_depth": 0})
    )

    # Exactly one ABSORBED (depth 0) and exactly one follow-on TRANSFORMED
    # (depth 1). The depth-1 event does NOT re-trigger a reaction (max_depth=1).
    absorbed = [d for (t, d) in seen if t == EventType.ABSORBED.value]
    transformed = [d for (t, d) in seen if t == EventType.TRANSFORMED.value]
    assert absorbed == [0]
    assert transformed == [1]
    # No deeper cascade: nothing at depth >= 2 ever appears.
    assert all((d or 0) < 2 for (_t, d) in seen)


def test_l3_depth_guard_blocks_event_already_at_max_depth(harness):
    vault = harness["vault"]
    bus = harness["bus"]

    vault.upsert(_flagged("m2"))
    # An ABSORBED already at _depth=1 with max_depth=1 must NOT fire (no route).
    bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="m2", payload={"_depth": 1})
    )
    after = vault.get("m2")
    assert after.phase is Phase.LIQUID
    assert after.reservoir is Reservoir.WORKING_STREAM


def test_l3_polluted_event_drives_filter_to_filtered(harness):
    vault = harness["vault"]
    bus = harness["bus"]

    # An already-polluted, contaminated droplet.
    bad = Droplet.from_dict(
        {
            "id": "bad1",
            "content": "that's wrong",
            "reservoir": "contaminated",
            "phase": "polluted",
            "state": {"purity": 0.2},
            "reason": "user corrected it",
        }
    )
    vault.upsert(bad)

    # POLLUTED -> filtration.filter (TRANSFORM): repaired into a filtered droplet.
    bus.publish(
        MemoryEvent(type=EventType.POLLUTED.value, droplet_id="bad1", payload={"_depth": 0})
    )
    after = vault.get("bad1")
    assert after.phase is Phase.FILTERED
    assert after.reservoir is Reservoir.SURFACE
    assert after.meta.get("usable_for_generation") is True


def test_l3_clean_droplet_absorbed_is_assessed_but_not_contaminated(harness):
    """A high-confidence droplet is assessed clean: stays usable, not routed.

    The detector finds no contamination, so ``assess_and_route`` leaves the
    routing (reservoir/phase) intact and only stamps ``contamination_checked``.
    That benign metadata change is a real ``to_dict`` diff, so the mesh records
    it and emits ONE follow-on — still a single hop, never routed to the
    contaminated pool and never marked unusable.
    """
    vault = harness["vault"]
    bus = harness["bus"]
    seen = harness["seen"]

    clean = Droplet.from_dict(
        {
            "id": "ok1",
            "content": "the sky is blue today",
            "reservoir": "working_stream",
            "state": {"purity": 0.9, "confidence": 0.9},
        }
    )
    vault.upsert(clean)
    bus.publish(
        MemoryEvent(type=EventType.ABSORBED.value, droplet_id="ok1", payload={"_depth": 0})
    )
    after = vault.get("ok1")
    # NOT routed: reservoir + phase intact, still usable for generation.
    assert after.reservoir is Reservoir.WORKING_STREAM
    assert after.phase is Phase.LIQUID
    assert after.meta.get("usable_for_generation") is not False
    # The assessment was recorded (clean verdict), and the cascade stayed bounded.
    assert after.meta.get("contamination_checked") is True
    assert all((d or 0) < 2 for (_t, d) in seen)
