"""Track B: end-to-end pipeline (PRD §14) with injected fakes.

Exercises ``process_experience`` (capture pipeline) and ``recall_for_agent``
(recall pipeline) with a fake repo + fake intelligence + monkeypatched
governance scorers / ``check_access``.
"""
from __future__ import annotations

from typing import Any

from hydromemory.governance import AccessContext, AgentIdentity
from hydromemory.governance.obligations import AccessDecision
from hydromemory.intelligence.base import (
    Abstractor,
    Classification,
    Classifier,
    ContaminationDetector,
    ContaminationVerdict,
    Embedder,
    Intelligence,
)
from hydromemory.pipeline import process_experience, recall_for_agent, route_to_reservoir
from hydromemory.recall import RecallMode
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import (
    STORABLE_PHASES,
    TRANSIENT_PHASES,
    Droplet,
    Permissions,
    Phase,
    State,
    Visibility,
)
from hydromemory.storage.repository import DropletRepository


# --- Fakes ------------------------------------------------------------------
class FakeEmbedder(Embedder):
    def embed(self, text: str) -> list[float]:
        return [float(len(text)), float(sum(ord(c) for c in text) % 53)]


class FakeAbstractor(Abstractor):
    def evaporate(self, content: str) -> str:
        return f"essence::{content[:8]}"


class FakeClassifier(Classifier):
    def __init__(self, sensitivity: float = 0.4) -> None:
        self._sensitivity = sensitivity

    def classify(self, content: str) -> Classification:
        return Classification("cognitive_style", 0.85, self._sensitivity, "persistent")


class FakeDetector(ContaminationDetector):
    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        return ContaminationVerdict(False, "ok", 0.9)


def make_intel(sensitivity: float = 0.4) -> Intelligence:
    return Intelligence(FakeEmbedder(), FakeAbstractor(), FakeClassifier(sensitivity), FakeDetector())


class FakeRepo(DropletRepository):
    def __init__(self) -> None:
        self.store: dict[str, Droplet] = {}
        self.links: list[tuple[str, str, str]] = []
        self.upserts: list[str] = []
        self.touched: list[str] = []
        self._similar: list[tuple[str, float]] = []

    def upsert(self, droplet: Droplet) -> None:
        self.store[droplet.id] = droplet
        self.upserts.append(droplet.id)

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
            if d is None:
                continue
            if candidate_filter is not None and not candidate_filter(d):
                continue
            out.append((did, cos))
        return out[:k]

    def add_link(self, src_id, kind, dst_id):
        self.links.append((src_id, kind, dst_id))

    def remove_link(self, src_id, kind, dst_id):
        self.links.remove((src_id, kind, dst_id))

    def touch_cycle(self, droplet_id, **kw):
        self.touched.append(droplet_id)

    def rebuild_index(self):
        pass

    def close(self):
        pass


def allow_all(droplet, agent, context, operation) -> AccessDecision:
    return AccessDecision(allowed=True)


def deny_all(droplet, agent, context, operation) -> AccessDecision:
    return AccessDecision(allowed=False, denial_reason="policy review failed")


# --- process_experience -----------------------------------------------------
def test_process_experience_stores_liquid_droplet():
    repo = FakeRepo()
    decision = process_experience(
        {"content": "User prefers architectural thinking.", "source": "conversation"},
        {"topic": "AI memory", "session_type": "design"},
        repo=repo,
        intelligence=make_intel(),
        check_access=allow_all,
    )
    assert decision["store"] is True
    assert decision["phase"] == Phase.LIQUID.value
    assert decision["droplet"]["memory_type"] == "cognitive_style"
    assert len(repo.store) == 1
    # context carried into the droplet meta.
    stored = next(iter(repo.store.values()))
    assert stored.meta["context"]["topic"] == "AI memory"


def test_process_experience_runs_full_step_order():
    repo = FakeRepo()
    # seed an existing droplet to be discovered as "related".
    existing = Droplet(id="old", content="prior")
    repo.upsert(existing)
    repo._similar = [("old", 0.7)]

    decision = process_experience(
        {"content": "new high-stakes idea"},
        {"urgency": True},  # -> PRESSURE trigger
        repo=repo,
        intelligence=make_intel(),
        check_access=allow_all,
    )
    assert "old" in decision["related"]
    assert ("pressure" in decision["triggers"])
    # an association edge was created to the related droplet.
    new_id = decision["droplet_id"]
    assert (new_id, "associations", "old") in repo.links


def test_process_experience_applies_phase_transition_via_trigger():
    repo = FakeRepo()
    # High emotional charge -> EXTREME_CHARGE -> LIQUID transitions to ICE.
    decision = process_experience(
        {"content": "traumatic", "charge": 0.95},
        {},
        repo=repo,
        intelligence=make_intel(),
        check_access=allow_all,
    )
    assert decision["phase"] == Phase.ICE.value


def test_process_experience_never_persists_transient_phase():
    """A fresh capture that walks LIQUID->...->RIVER must settle to a STORABLE phase.

    Firing HEAT+SIMILARITY+DENSITY+ASSOCIATION drives a brand-new droplet all the
    way to RIVER in one pass, but REPETITION (River->Groundwater) needs
    ``cycle_count >= 3`` and a fresh droplet is at 0 -- so without settling it
    would rest in RIVER, a TRANSIENT_PHASES member ADR-0003 forbids on write.
    The pipeline must settle it (RIVER -> GROUNDWATER) before upsert.
    """
    repo = FakeRepo()
    decision = process_experience(
        {"content": "an idea that clusters, precipitates, and associates"},
        {
            "attention": True,        # -> HEAT  (LIQUID -> VAPOR)
            "similarity": 0.9,        # -> SIMILARITY (VAPOR -> CLOUD)
            "density": 0.9,           # -> DENSITY (CLOUD -> RAIN)
            "association": True,      # -> ASSOCIATION (RAIN -> RIVER)
            # cycle_count defaults to 0 -> REPETITION guard blocks (stays RIVER).
        },
        repo=repo,
        intelligence=make_intel(),
        check_access=allow_all,
    )
    # The resting phase is storable, never transient.
    rested = Phase(decision["phase"])
    assert rested in STORABLE_PHASES
    assert rested not in TRANSIENT_PHASES
    # RIVER settles to its downstream storable phase, GROUNDWATER.
    assert rested is Phase.GROUNDWATER
    # And the persisted droplet agrees.
    stored = next(iter(repo.store.values()))
    assert stored.phase is Phase.GROUNDWATER
    assert stored.phase in STORABLE_PHASES


def test_process_experience_blocked_by_governance_is_not_stored():
    repo = FakeRepo()
    decision = process_experience(
        {"content": "blocked memory"},
        {},
        repo=repo,
        intelligence=make_intel(),
        check_access=deny_all,
    )
    assert decision["store"] is False
    assert len(repo.store) == 0
    assert decision["decision"]["allowed"] is False


def test_process_experience_default_check_access_does_not_crash():
    """Pipeline runs with the default governance ``check_access``.

    Track C may or may not have wired ``check_access`` yet. Either way the
    pipeline must produce a valid decision dict: if governance is still the
    NotImplementedError stub it is tolerated (default-allow, review skipped);
    if it's live, a real allow/deny decision flows through.
    """
    repo = FakeRepo()
    decision = process_experience(
        {"content": "x"},
        {},
        repo=repo,
        intelligence=make_intel(),
    )
    assert isinstance(decision["decision"], dict)
    assert "allowed" in decision["decision"]
    # store mirrors the decision's allow flag.
    assert decision["store"] is bool(decision["decision"]["allowed"])


def test_route_to_reservoir_sensitive_to_sacred():
    d = Droplet(id="s")
    assert route_to_reservoir(d, classification_sensitivity=0.95) is Reservoir.SACRED


def test_route_to_reservoir_contaminated():
    d = Droplet(id="c", phase=Phase.POLLUTED)
    assert route_to_reservoir(d) is Reservoir.CONTAMINATED


def test_route_to_reservoir_default_working_stream():
    assert route_to_reservoir(Droplet(id="w"), 0.1) is Reservoir.WORKING_STREAM


# --- recall_for_agent -------------------------------------------------------
def _seed_recallable(repo: FakeRepo) -> Droplet:
    d = Droplet(
        id="hit",
        content="User prefers depth for architecture.",
        phase=Phase.LIQUID,
        reservoir=Reservoir.WORKING_STREAM,
        semantic_tags=["AI memory"],
        state=State(purity=0.9, gravity=0.5, confidence=0.9),
        permissions=Permissions(allowed_agents=["assistant"]),
    )
    repo.upsert(d)
    repo._similar = [("hit", 0.85)]
    return d


def test_recall_for_agent_returns_ranked_results(monkeypatch):
    repo = FakeRepo()
    _seed_recallable(repo)
    agent = AgentIdentity(name="assistant")

    results = recall_for_agent(
        "how should I explain architecture?",
        agent,
        {"topic": "AI memory"},
        repo=repo,
        intelligence=make_intel(),
        permission_score=lambda d, a: 1.0,
        privacy_risk=lambda d, c=None: 0.1,
    )
    assert len(results) == 1
    assert results[0].droplet_id == "hit"
    assert results[0].score > 0
    assert results[0].mode in set(RecallMode)


def test_recall_for_agent_permission_gate_excludes_unauthorized():
    repo = FakeRepo()
    _seed_recallable(repo)
    agent = AgentIdentity(name="stranger")

    results = recall_for_agent(
        "query",
        agent,
        {},
        repo=repo,
        intelligence=make_intel(),
        permission_score=lambda d, a: 0.0,  # gate blocks everything
        privacy_risk=lambda d, c=None: 0.0,
    )
    assert results == []


def test_recall_for_agent_drops_below_threshold():
    repo = FakeRepo()
    # A groundwater/glacier droplet with a low semantic hit + heavy privacy -> below threshold.
    d = Droplet(
        id="deep",
        content="buried",
        phase=Phase.GROUNDWATER,
        reservoir=Reservoir.GLACIER,
        state=State(purity=0.1, depth=0.9),
        permissions=Permissions(visibility=Visibility.PRIVATE),
    )
    repo.upsert(d)
    repo._similar = [("deep", 0.05)]
    agent = AgentIdentity(name="assistant")

    results = recall_for_agent(
        "unrelated", agent, {}, repo=repo, intelligence=make_intel(),
        permission_score=lambda d, a: 0.1,
        privacy_risk=lambda d, c=None: 1.0,
    )
    assert results == []


def test_recall_for_agent_ranks_by_score():
    repo = FakeRepo()
    high = Droplet(id="high", content="strong", phase=Phase.LIQUID, reservoir=Reservoir.WORKING_STREAM,
                   semantic_tags=["AI memory"], state=State(purity=0.95, gravity=0.8, confidence=0.9),
                   permissions=Permissions(allowed_agents=["assistant"]))
    low = Droplet(id="low", content="weak", phase=Phase.LIQUID, reservoir=Reservoir.WORKING_STREAM,
                  state=State(purity=0.6, confidence=0.9),
                  permissions=Permissions(allowed_agents=["assistant"]))
    repo.upsert(high)
    repo.upsert(low)
    repo._similar = [("low", 0.4), ("high", 0.9)]
    agent = AgentIdentity(name="assistant")

    results = recall_for_agent(
        "q", agent, {"topic": "AI memory"}, repo=repo, intelligence=make_intel(),
        permission_score=lambda d, a: 1.0,
        privacy_risk=lambda d, c=None: 0.1,
    )
    assert [r.droplet_id for r in results] == ["high", "low"]
    assert results[0].score >= results[1].score


def test_recall_for_agent_uses_fallback_scorers_when_governance_absent():
    repo = FakeRepo()
    _seed_recallable(repo)
    agent = AgentIdentity(name="assistant")
    # No scorers injected -> pipeline falls back to internal scorers (Track C absent).
    results = recall_for_agent(
        "query", agent,
        AccessContext(),
        repo=repo,
        intelligence=make_intel(),
    )
    assert len(results) == 1
    assert results[0].droplet_id == "hit"
