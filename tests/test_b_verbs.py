"""Track B: the 15 API verbs (PRD §5.7) with mocked dependencies.

Every verb is exercised with fakes for repo / intelligence / governance /
forgetting / contamination. Co-owned verbs (FREEZE/FILTER/POLLUTE/DRAIN/
ARCHIVE/FORGET) are asserted to *delegate* to the injected modules.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from hydromemory.intelligence.base import (
    Abstractor,
    Classification,
    Classifier,
    ContaminationDetector,
    ContaminationVerdict,
    Embedder,
    Intelligence,
)
from hydromemory.protocol import ProtocolResponse
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, State
from hydromemory.storage.repository import DropletRepository
from hydromemory.verbs import Verbs


# --- Fakes ------------------------------------------------------------------
class FakeEmbedder(Embedder):
    def embed(self, text: str) -> list[float]:
        # Deterministic toy embedding from char codes.
        return [float(sum(ord(c) for c in text) % 97), float(len(text))]


class FakeAbstractor(Abstractor):
    def evaporate(self, content: str) -> str:
        return f"pattern<{content[:12]}>"


class FakeClassifier(Classifier):
    def classify(self, content: str) -> Classification:
        return Classification(
            memory_type="communication_preference",
            importance=0.8,
            sensitivity=0.4,
            expected_lifespan="persistent",
        )


class FakeDetector(ContaminationDetector):
    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        return ContaminationVerdict(contaminated=False, reason="ok", confidence=0.9)


def fake_intelligence() -> Intelligence:
    return Intelligence(FakeEmbedder(), FakeAbstractor(), FakeClassifier(), FakeDetector())


class FakeRepo(DropletRepository):
    """In-memory DropletRepository for unit tests."""

    def __init__(self) -> None:
        self.store: dict[str, Droplet] = {}
        self.links: list[tuple[str, str, str]] = []
        self.cycle_calls: list[tuple[str, bool]] = []
        self.deleted: list[str] = []
        self._similar: list[tuple[str, float]] = []

    def upsert(self, droplet: Droplet) -> None:
        self.store[droplet.id] = droplet

    def get(self, droplet_id: str) -> Droplet | None:
        return self.store.get(droplet_id)

    def delete(self, droplet_id: str) -> None:
        self.deleted.append(droplet_id)
        self.store.pop(droplet_id, None)

    def all_ids(self) -> list[str]:
        return list(self.store)

    def query(self, **kwargs: Any) -> list[Droplet]:
        return list(self.store.values())

    def search_similar(self, embedding, k=10, candidate_filter=None):
        out = []
        for did, cos in self._similar:
            d = self.store.get(did)
            if d is None:
                continue
            if candidate_filter is not None and not candidate_filter(d):
                continue
            out.append((did, cos))
        return out[:k]

    def add_link(self, src_id: str, kind: str, dst_id: str) -> None:
        self.links.append((src_id, kind, dst_id))

    def remove_link(self, src_id: str, kind: str, dst_id: str) -> None:
        self.links.remove((src_id, kind, dst_id))

    def touch_cycle(self, droplet_id, *, recalled=None, transformed=None, verified=None, increment_count=False):
        self.cycle_calls.append((droplet_id, increment_count))

    def rebuild_index(self) -> None:
        pass

    def close(self) -> None:
        pass


def fake_forgetting() -> types.SimpleNamespace:
    calls: list[str] = []

    def drain(d: Droplet, **kw: Any) -> Droplet:
        calls.append("drain")
        d.meta["drained"] = True
        return d

    def sediment(d: Droplet, **kw: Any) -> Droplet:
        calls.append("sediment")
        d.meta["sedimented"] = True
        return d

    def seal(d: Droplet, **kw: Any) -> Droplet:
        calls.append("seal")
        d.meta["sealed"] = True
        return d

    def delete(d: Droplet) -> None:
        calls.append("delete")

    return types.SimpleNamespace(drain=drain, sediment=sediment, seal=seal, delete=delete, calls=calls)


def fake_contamination() -> types.SimpleNamespace:
    calls: list[str] = []

    def mark_polluted(d: Droplet, reason: str) -> Droplet:
        calls.append("mark_polluted")
        d.phase = Phase.POLLUTED
        d.reservoir = Reservoir.CONTAMINATED
        d.meta["reason"] = reason
        return d

    def filter_droplet(d: Droplet, detector=None) -> Droplet:
        calls.append("filter_droplet")
        d.phase = Phase.FILTERED
        d.state.purity = 0.95
        return d

    return types.SimpleNamespace(mark_polluted=mark_polluted, filter_droplet=filter_droplet, calls=calls)


@pytest.fixture
def verbs_kit():
    repo = FakeRepo()
    forg = fake_forgetting()
    cont = fake_contamination()
    verbs = Verbs(
        repo=repo,
        intelligence=fake_intelligence(),
        forgetting=forg,
        contamination=cont,
    )
    return verbs, repo, forg, cont


# --- 1. ABSORB --------------------------------------------------------------
def test_absorb_creates_liquid_droplet(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = verbs.absorb("User prefers depth", context={"topic": "AI"})
    assert d.phase is Phase.LIQUID
    assert d.memory_type == "communication_preference"
    assert d.embedding is not None
    assert repo.store[d.id] is d
    assert d.meta["importance"] == 0.8


# --- 2. FLOW ----------------------------------------------------------------
def test_flow_adds_association_links(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="a")
    verbs.flow(d, ["b", "c", "a"])  # self-link skipped
    assert d.links.associations == ["b", "c"]
    assert ("a", "associations", "b") in repo.links
    assert ("a", "associations", "a") not in repo.links


# --- 3. EVAPORATE -----------------------------------------------------------
def test_evaporate_creates_vapor_with_derived_link(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    src = Droplet(id="src", content="long content here")
    vapor = verbs.evaporate(src)
    assert vapor.phase is Phase.VAPOR
    assert "src" in vapor.links.derived_from
    assert vapor.content.startswith("pattern<")


# --- 4. CONDENSE ------------------------------------------------------------
def test_condense_clusters_vapors_to_cloud(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    v1 = Droplet(id="v1", phase=Phase.VAPOR, content="a")
    v2 = Droplet(id="v2", phase=Phase.VAPOR, content="b")
    cloud = verbs.condense([v1, v2])
    assert cloud.phase is Phase.CLOUD
    assert set(cloud.links.derived_from) == {"v1", "v2"}
    assert cloud.meta["members"] == ["v1", "v2"]


def test_condense_requires_members(verbs_kit):
    verbs, *_ = verbs_kit
    with pytest.raises(ValueError):
        verbs.condense([])


# --- 5. PRECIPITATE (recall path) ------------------------------------------
def test_precipitate_returns_protocol_response(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="hit", phase=Phase.LIQUID, content="X", state=State(purity=0.9, gravity=0.5))
    repo.upsert(d)
    repo._similar = [("hit", 0.9)]
    resp = verbs.precipitate("query", agent="assistant", query_ctx={"topic": "AI"})
    assert isinstance(resp, ProtocolResponse)
    assert resp.operation == "PRECIPITATE"
    assert resp.outcome["recalled"] >= 1


# --- 6. INFILTRATE ----------------------------------------------------------
def test_infiltrate_river_to_groundwater(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="r", phase=Phase.RIVER)
    verbs.infiltrate(d)
    assert d.phase is Phase.GROUNDWATER
    assert d.reservoir is Reservoir.GROUNDWATER
    assert d.state.depth > 0.0


def test_infiltrate_liquid_deepens(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="l", phase=Phase.LIQUID, state=State(depth=0.1))
    verbs.infiltrate(d)
    assert d.state.depth > 0.1
    assert d.reservoir is Reservoir.GROUNDWATER


# --- 7. FREEZE (delegates policy review to governance) ---------------------
def test_freeze_creates_ice_snapshot(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="f", phase=Phase.LIQUID, state=State(integrity=0.5))
    verbs.freeze(d)
    assert d.phase is Phase.ICE
    assert d.reservoir is Reservoir.GLACIER
    assert d.state.integrity > 0.5


def test_freeze_blocked_by_governance_denial():
    repo = FakeRepo()

    def deny(droplet, agent, context, operation):
        return types.SimpleNamespace(allowed=False, denial_reason="identity write blocked")

    verbs = Verbs(repo=repo, intelligence=fake_intelligence(), check_access=deny)
    d = Droplet(id="f", phase=Phase.LIQUID)
    out = verbs.freeze(d, agent=types.SimpleNamespace(name="bot"), context=None)
    assert out.phase is Phase.LIQUID  # unchanged
    assert out.meta["freeze_denied"] == "identity write blocked"


# --- 8. MELT ----------------------------------------------------------------
def test_melt_thaws_ice_when_safe(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="i", phase=Phase.ICE)
    verbs.melt(d, context={"safe_context": True})
    assert d.phase is Phase.LIQUID
    assert d.reservoir is Reservoir.WORKING_STREAM


def test_melt_blocked_when_unsafe(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="i", phase=Phase.ICE)
    verbs.melt(d, context={"safe_context": False})
    assert d.phase is Phase.ICE
    assert d.meta["melt_blocked"]


# --- 9. FILTER (delegates to contamination) --------------------------------
def test_filter_delegates_to_contamination(verbs_kit):
    verbs, repo, _, cont = verbs_kit
    d = Droplet(id="p", phase=Phase.POLLUTED, state=State(purity=0.2))
    out = verbs.filter(d)
    assert "filter_droplet" in cont.calls
    assert out.phase is Phase.FILTERED
    assert out.state.purity == 0.95


def test_filter_requires_module():
    verbs = Verbs(repo=FakeRepo(), intelligence=fake_intelligence())
    with pytest.raises(RuntimeError):
        verbs.filter(Droplet(id="x"))


# --- 10. POLLUTE (delegates to contamination) ------------------------------
def test_pollute_delegates_to_contamination(verbs_kit):
    verbs, repo, _, cont = verbs_kit
    d = Droplet(id="d", phase=Phase.LIQUID)
    out = verbs.pollute(d, "bad source")
    assert "mark_polluted" in cont.calls
    assert out.phase is Phase.POLLUTED
    assert out.meta["reason"] == "bad source"


# --- 11. DISTILL ------------------------------------------------------------
def test_distill_extracts_principle(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    c1 = Droplet(id="c1", content="x", state=State(purity=0.7, gravity=0.5))
    c2 = Droplet(id="c2", content="y", state=State(purity=0.8, gravity=0.6))
    principle = verbs.distill([c1, c2])
    # Distilled principles land in CLOUD (the abstraction layer), not SACRED, so
    # ordinary approved agents can reuse them at recall (ADR-0036).
    assert principle.reservoir is Reservoir.CLOUD
    assert principle.meta["principle"]
    assert set(principle.meta["distilled_from"]) == {"c1", "c2"}
    assert principle.state.purity > 0.8


# --- 12. IRRIGATE -----------------------------------------------------------
def test_irrigate_increments_cycle(verbs_kit):
    verbs, repo, _, _ = verbs_kit
    d = Droplet(id="d")
    verbs.irrigate(d, task="new task")
    assert d.cycle.cycle_count == 1
    assert ("d", True) in repo.cycle_calls
    assert "new task" in d.meta["applied_to"]


# --- 13. DRAIN (delegates to forgetting) -----------------------------------
def test_drain_delegates_to_forgetting(verbs_kit):
    verbs, repo, forg, _ = verbs_kit
    d = Droplet(id="d")
    out = verbs.drain(d)
    assert "drain" in forg.calls
    assert out.meta["drained"] is True


# --- 14. ARCHIVE (delegates to forgetting sediment/seal) -------------------
def test_archive_sediment(verbs_kit):
    verbs, repo, forg, _ = verbs_kit
    out = verbs.archive(Droplet(id="d"))
    assert "sediment" in forg.calls
    assert out.meta["sedimented"] is True


def test_archive_seal(verbs_kit):
    verbs, repo, forg, _ = verbs_kit
    out = verbs.archive(Droplet(id="d"), seal=True)
    assert "seal" in forg.calls
    assert out.meta["sealed"] is True


# --- 15. FORGET (governance-checked delete) --------------------------------
def test_forget_deletes_when_allowed(verbs_kit):
    verbs, repo, forg, _ = verbs_kit
    d = Droplet(id="gone")
    repo.upsert(d)
    resp = verbs.forget(d)
    assert isinstance(resp, ProtocolResponse)
    assert resp.result is True
    assert "delete" in forg.calls
    assert "gone" in repo.deleted


def test_forget_blocked_by_governance():
    repo = FakeRepo()
    forg = fake_forgetting()

    def deny(droplet, agent, context, operation):
        return types.SimpleNamespace(allowed=False, denial_reason="protected", to_dict=lambda: {"allowed": False})

    verbs = Verbs(repo=repo, intelligence=fake_intelligence(), forgetting=forg, check_access=deny)
    d = Droplet(id="keep")
    repo.upsert(d)
    resp = verbs.forget(d, agent=types.SimpleNamespace(name="bot"), context=None)
    assert resp.result is False
    assert "delete" not in forg.calls
    assert "keep" not in repo.deleted
