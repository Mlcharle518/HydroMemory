"""Autonomic consolidation (ADR-0031): the MeshEngine `cluster`/`distill`
surfaces and the DistillationAgent running them via the tick cadence.

Before this, `DistillationAgent` called `engine.cluster(...)` against a surface
that had no implementation (AttributeError on the mesh). These tests prove the
primitive is real and that the agent now clusters a constellation and distills
one reusable principle per connected component.
"""
from __future__ import annotations

from typing import Any

import pytest

from hydromemory.agents.base import AgentContext
from hydromemory.agents.distillation import DistillationAgent
from hydromemory.agents.registry import AgentRuntime
from hydromemory.intelligence.base import (
    Abstractor,
    Classification,
    Classifier,
    ContaminationDetector,
    ContaminationVerdict,
    Embedder,
    Intelligence,
)
from hydromemory.platform.runtime import MeshEngine
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, State


class _Emb(Embedder):
    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


class _Abs(Abstractor):
    def evaporate(self, content: str) -> str:
        return f"principle<{content[:20]}>"


class _Cls(Classifier):
    def classify(self, content: str) -> Classification:
        return Classification("pref", 0.5, 0.2, "persistent")


class _Det(ContaminationDetector):
    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        return ContaminationVerdict(False, "ok", 0.9)


def _intel() -> Intelligence:
    return Intelligence(_Emb(), _Abs(), _Cls(), _Det())


def _d(did: str, *, purity: float = 0.7, **links: list[str]) -> Droplet:
    d = Droplet(
        id=did,
        content=did,
        phase=Phase.LIQUID,
        state=State(purity=purity, gravity=0.4, integrity=0.6, confidence=0.8),
    )
    for kind, ids in links.items():
        setattr(d.links, kind, list(ids))
    return d


# --- MeshEngine.cluster -----------------------------------------------------
def test_meshengine_cluster_groups_connected_components():
    eng = MeshEngine(_intel())
    a = _d("a", associations=["b"])
    b = _d("b", supports=["c"])
    c = _d("c")
    d = _d("d")  # isolated
    groups = eng.cluster([a, b, c, d])
    gsets = sorted(sorted(x.id for x in g) for g in groups)
    assert gsets == [["a", "b", "c"], ["d"]]


def test_meshengine_cluster_excludes_contradictions():
    eng = MeshEngine(_intel())
    a = _d("a", contradictions=["b"])  # a contradiction must NOT merge a and b
    b = _d("b")
    groups = eng.cluster([a, b])
    gsets = sorted(sorted(x.id for x in g) for g in groups)
    assert gsets == [["a"], ["b"]]


# --- MeshEngine.distill -----------------------------------------------------
def test_meshengine_distill_builds_cloud_principle():
    eng = MeshEngine(_intel())
    a = _d("a", purity=0.7)
    b = _d("b", purity=0.8)
    p = eng.distill([a, b])
    # CLOUD (abstraction layer), GROUNDWATER phase — reusable by approved agents (ADR-0036).
    assert p.reservoir is Reservoir.CLOUD
    assert p.phase is Phase.GROUNDWATER
    assert set(p.links.derived_from) == {"a", "b"}
    assert p.meta["distilled_from"] == ["a", "b"]
    assert p.state.purity > 0.8  # max source purity + 0.05
    assert p.meta["principle"]
    assert p.embedding is not None


def test_meshengine_distill_empty_raises():
    eng = MeshEngine(_intel())
    with pytest.raises(ValueError):
        eng.distill([])


# --- DistillationAgent via the tick cadence ---------------------------------
def test_distillation_agent_consolidates_via_tick():
    eng = MeshEngine(_intel())
    runtime = AgentRuntime()
    runtime.register(DistillationAgent(eng))  # only this role, so no other engine surface is hit

    a = _d("a", associations=["b"])
    b = _d("b", supports=["c"])
    c = _d("c")
    d = _d("d")  # isolated -> its own principle
    ctx = AgentContext(payload={"droplets": [a, b, c, d]})

    runtime.tick("distill", ctx)
    principles = ctx.results["distillation"]

    assert len(principles) == 2  # one principle per connected component
    assert all(p.reservoir is Reservoir.CLOUD for p in principles)  # ADR-0036
    # every input droplet is accounted for in some principle's provenance
    provenance = {pid for p in principles for pid in p.meta["distilled_from"]}
    assert provenance == {"a", "b", "c", "d"}
