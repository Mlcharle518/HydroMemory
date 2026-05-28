"""Spreading-activation spine (docs/closing-the-gaps.md §4): query-conditioned
traversal over the links graph, the hydraulic decay model, the cluster primitive,
and the opt-in multi-hop expansion of ``precipitate``.

The pure-function tests need no repository (activation.py injects its accessors);
the integration test drives the real ``Verbs.precipitate`` over a tiny in-memory
repo to prove candidate expansion surfaces a droplet the cosine top-k missed.
"""
from __future__ import annotations

from typing import Any

from hydromemory.activation import (
    DEFAULT_ACTIVATION_PARAMS,
    ActivationParams,
    cluster,
    effective_edge_weight,
    spread_activation,
)
from hydromemory.intelligence.base import (
    Abstractor,
    Classification,
    Classifier,
    ContaminationDetector,
    ContaminationVerdict,
    Embedder,
    Intelligence,
)
from hydromemory.recall import RecallWeights
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, State
from hydromemory.storage.repository import DropletRepository
from hydromemory.verbs import Verbs


# --- helpers ----------------------------------------------------------------
def _graph(edges: dict[str, list[tuple[str, str]]]):
    def neighbors(i: str) -> list[tuple[str, str]]:
        return edges.get(i, [])

    return neighbors


def _states(table: dict[str, State]):
    def state_of(i: str) -> State | None:
        return table.get(i)

    return state_of


# --- spread_activation: reachability + seeds --------------------------------
def test_seeds_are_included():
    act = spread_activation({"s": 0.7}, _graph({}), _states({"s": State()}))
    assert act["s"] == 0.7


def test_two_hop_node_is_reached():
    edges = {"seed": [("hop", "associations")], "hop": []}
    states = {"seed": State(fluidity=0.9, purity=1.0), "hop": State(depth=0.0)}
    act = spread_activation({"seed": 0.9}, _graph(edges), _states(states))
    assert act["hop"] > 0.0  # the multi-hop win: reached via a link, not cosine


# --- hydraulic decay model --------------------------------------------------
def test_resistance_depth_damps_delivery():
    edges = {"seed": [("t", "associations")], "t": []}
    low = {"seed": State(fluidity=1.0, purity=1.0), "t": State(depth=0.0)}
    high = {"seed": State(fluidity=1.0, purity=1.0), "t": State(depth=0.9)}
    a_low = spread_activation({"seed": 1.0}, _graph(edges), _states(low))
    a_high = spread_activation({"seed": 1.0}, _graph(edges), _states(high))
    assert a_low["t"] > a_high.get("t", 0.0)


def test_zero_fluidity_source_conducts_nothing():
    edges = {"seed": [("t", "associations")], "t": []}
    states = {"seed": State(fluidity=0.0, purity=1.0), "t": State()}
    act = spread_activation({"seed": 1.0}, _graph(edges), _states(states))
    assert "t" not in act


def test_low_purity_source_contributes_less():
    """Epistemic hygiene: a contaminated node floods the constellation less."""
    edges = {"seed": [("t", "associations")], "t": []}
    pure = {"seed": State(fluidity=1.0, purity=1.0), "t": State(depth=0.0)}
    dirty = {"seed": State(fluidity=1.0, purity=0.2), "t": State(depth=0.0)}
    a_pure = spread_activation({"seed": 1.0}, _graph(edges), _states(pure))
    a_dirty = spread_activation({"seed": 1.0}, _graph(edges), _states(dirty))
    assert a_dirty["t"] < a_pure["t"]


# --- intent -> edge selection -----------------------------------------------
def test_effective_edge_weight_intent_gating():
    p = DEFAULT_ACTIVATION_PARAMS
    assert effective_edge_weight("associations", None, p) == 1.0  # no intent -> base
    assert effective_edge_weight("associations", "exception", p) == 0.0  # gated out
    assert effective_edge_weight("contradictions", "exception", p) == 0.6  # preferred
    assert effective_edge_weight("associations", "mystery_intent", p) == 1.0  # unknown -> base


def test_intent_steers_which_links_conduct():
    edges = {"seed": [("hop", "associations")], "hop": []}
    states = {"seed": State(fluidity=0.9, purity=1.0), "hop": State(depth=0.0)}
    assert "hop" in spread_activation({"seed": 0.9}, _graph(edges), _states(states))
    # An "exception" question follows contradictions/derived_from, not associations.
    gated = spread_activation({"seed": 0.9}, _graph(edges), _states(states), intent="exception")
    assert "hop" not in gated


# --- termination bound ------------------------------------------------------
def test_max_hops_bounds_radius():
    edges = {
        "seed": [("a", "associations")],
        "a": [("b", "associations")],
        "b": [("c", "associations")],
        "c": [],
    }
    st = _states({k: State(fluidity=1.0, purity=1.0, depth=0.0) for k in "seed a b c".split()})
    params = ActivationParams(max_hops=2, min_activation=0.0, decay=0.9)
    act = spread_activation({"seed": 1.0}, _graph(edges), st, params=params)
    assert "a" in act and "b" in act
    assert "c" not in act  # 3rd hop never runs


# --- cluster primitive ------------------------------------------------------
def test_cluster_connected_components_excludes_contradictions():
    a, b, c, d = (Droplet(id=i) for i in "a b c d".split())
    edges = {
        "a": [("b", "associations"), ("d", "contradictions")],  # contradiction must NOT merge
        "b": [("c", "supports")],
        "c": [],
        "d": [],
    }
    groups = cluster([a, b, c, d], _graph(edges))
    gsets = sorted(sorted(x.id for x in g) for g in groups)
    assert gsets == [["a", "b", "c"], ["d"]]


# --- integration: precipitate multi-hop expansion ---------------------------
class _Embedder(Embedder):
    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


class _Abstractor(Abstractor):
    def evaporate(self, content: str) -> str:
        return content[:8]


class _Classifier(Classifier):
    def classify(self, content: str) -> Classification:
        return Classification("pref", 0.5, 0.2, "persistent")


class _Detector(ContaminationDetector):
    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        return ContaminationVerdict(False, "ok", 0.9)


def _intel() -> Intelligence:
    return Intelligence(_Embedder(), _Abstractor(), _Classifier(), _Detector())


class _Repo(DropletRepository):
    def __init__(self) -> None:
        self.store: dict[str, Droplet] = {}
        self._similar: list[tuple[str, float]] = []

    def upsert(self, droplet: Droplet) -> None:
        self.store[droplet.id] = droplet

    def get(self, droplet_id: str) -> Droplet | None:
        return self.store.get(droplet_id)

    def delete(self, droplet_id: str) -> None:
        self.store.pop(droplet_id, None)

    def all_ids(self) -> list[str]:
        return list(self.store)

    def query(self, **kwargs: Any) -> list[Droplet]:
        return list(self.store.values())

    def search_similar(self, embedding, k=10, candidate_filter=None):
        out = []
        for did, cos in self._similar:
            d = self.store.get(did)
            if d is None or (candidate_filter is not None and not candidate_filter(d)):
                continue
            out.append((did, cos))
        return out[:k]

    def add_link(self, src_id: str, kind: str, dst_id: str) -> None:
        pass

    def remove_link(self, src_id: str, kind: str, dst_id: str) -> None:
        pass

    def touch_cycle(self, droplet_id, *, recalled=None, transformed=None, verified=None, increment_count=False):
        pass

    def rebuild_index(self) -> None:
        pass

    def close(self) -> None:
        pass


def _mk(did: str, **state: float) -> Droplet:
    return Droplet(
        id=did,
        phase=Phase.LIQUID,
        reservoir=Reservoir.WORKING_STREAM,
        content=did,
        state=State(**state),
    )


def _setup_linked_repo() -> _Repo:
    repo = _Repo()
    seed = _mk("seed", purity=1.0, fluidity=0.9, gravity=0.4, pressure=0.3)
    seed.links.associations = ["hop"]
    hop = _mk("hop", purity=1.0, fluidity=0.5, gravity=0.4, pressure=0.3, depth=0.0)
    repo.upsert(seed)
    repo.upsert(hop)
    repo._similar = [("seed", 0.9)]  # only the seed is a cosine hit
    return repo


def test_precipitate_default_does_not_traverse():
    verbs = Verbs(repo=_setup_linked_repo(), intelligence=_intel())
    resp = verbs.precipitate("q", agent="assistant")
    ids = {r.droplet_id for r in resp.result}
    assert ids == {"seed"}  # the linked hop is never a candidate without traversal


def test_precipitate_traverse_surfaces_linked_hop():
    verbs = Verbs(repo=_setup_linked_repo(), intelligence=_intel())
    resp = verbs.precipitate("q", agent="assistant", traverse=True)
    ids = {r.droplet_id for r in resp.result}
    assert ids == {"seed", "hop"}  # multi-hop: hop surfaced via the link


def test_activation_bonus_raises_activated_score():
    verbs = Verbs(repo=_setup_linked_repo(), intelligence=_intel())
    base = verbs.precipitate("q", agent="assistant", traverse=True)
    boosted = verbs.precipitate(
        "q", agent="assistant", traverse=True, weights=RecallWeights(activation_bonus=2.0)
    )
    hop_base = next(r.score for r in base.result if r.droplet_id == "hop")
    hop_boost = next(r.score for r in boosted.result if r.droplet_id == "hop")
    assert hop_boost > hop_base


def test_intent_gating_blocks_traversal_in_precipitate():
    verbs = Verbs(repo=_setup_linked_repo(), intelligence=_intel())
    # The seed->hop edge is an association; an "exception" intent gates it out.
    resp = verbs.precipitate("q", agent="assistant", traverse=True, query_ctx={"intent": "exception"})
    ids = {r.droplet_id for r in resp.result}
    assert ids == {"seed"}
