"""Track C agent-role tests (PRD §8): each of the eight roles calls the expected
engine verbs, and ``AgentRuntime.tick`` invokes roles synchronously in order.

The engine is a hand-rolled recording mock implementing the union of the narrow
duck-typed surfaces each role uses; tests assert which methods were called.
"""
from __future__ import annotations

from typing import Any

from hydromemory.agents import (
    AgentContext,
    AgentRuntime,
    ArchivistAgent,
    CaptureAgent,
    DistillationAgent,
    FiltrationAgent,
    HydrologistAgent,
    PrivacyAgent,
    RecallAgent,
    ReflectionAgent,
    build_default_runtime,
)
from hydromemory.governance import AccessDecision, Operation, TrustLevel
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase


class RecordingEngine:
    """Records calls and returns simple deterministic values."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _rec(self, name: str, *args: Any, **kw: Any) -> None:
        self.calls.append((name, args, kw))

    def method_names(self) -> list[str]:
        return [c[0] for c in self.calls]

    # capture
    def propose_droplet(self, event: Any) -> Droplet:
        self._rec("propose_droplet", event)
        return Droplet(id=f"mem_{event}", content=str(event))

    # hydrologist
    def detect_triggers(self, droplet: Any, context: dict) -> list[str]:
        self._rec("detect_triggers", droplet, context)
        return ["precipitate"]

    def apply_transition(self, droplet: Any, transition: str) -> Droplet:
        self._rec("apply_transition", droplet, transition)
        return droplet

    # filtration
    def assess_and_route(self, droplet: Any, context: dict) -> Droplet:
        self._rec("assess_and_route", droplet, context)
        return droplet

    def filter(self, droplet: Any) -> Droplet:
        self._rec("filter", droplet)
        return droplet

    # recall / privacy
    def search(self, query: dict) -> list[Droplet]:
        self._rec("search", query)
        return [Droplet(id="mem_a"), Droplet(id="mem_b")]

    def check_access(self, droplet, agent, context, operation) -> AccessDecision:
        self._rec("check_access", droplet, agent, context, operation)
        # deny mem_b so we can prove recall filters it out.
        return AccessDecision(allowed=(droplet.id != "mem_b"))

    def rank(self, droplets: list, query: dict) -> list:
        self._rec("rank", droplets, query)
        return list(reversed(droplets))

    def privacy_risk(self, droplet, context) -> float:
        self._rec("privacy_risk", droplet, context)
        return 0.42

    # reflection
    def aged_droplets(self, context: dict) -> list[Droplet]:
        self._rec("aged_droplets", context)
        return [Droplet(id="mem_old")]

    def reverify(self, droplet: Any) -> Droplet:
        self._rec("reverify", droplet)
        return droplet

    # distillation
    def cluster(self, droplets: list, context: dict) -> list[list]:
        self._rec("cluster", droplets, context)
        return [droplets]

    def distill(self, cluster: Any) -> Droplet:
        self._rec("distill", cluster)
        return Droplet(id="mem_principle")

    # archivist
    def freeze(self, droplet: Any) -> Droplet:
        self._rec("freeze", droplet)
        return droplet

    def sediment(self, droplet: Any) -> Droplet:
        self._rec("sediment", droplet)
        return droplet

    def delete(self, droplet: Any) -> None:
        self._rec("delete", droplet)
        return None


def test_capture_proposes_droplets_per_event():
    eng = RecordingEngine()
    agent = CaptureAgent(eng)
    ctx = AgentContext(stage="capture", payload={"events": ["e1", "e2"]})
    out = agent.run(ctx)
    assert eng.method_names() == ["propose_droplet", "propose_droplet"]
    assert len(out) == 2
    assert ctx.data["proposed"] == out


def test_hydrologist_detects_and_applies_transitions():
    eng = RecordingEngine()
    agent = HydrologistAgent(eng)
    d = Droplet(id="mem_1")
    agent.run(AgentContext(stage="maintain", payload={"droplets": [d]}))
    names = eng.method_names()
    assert "detect_triggers" in names
    assert "apply_transition" in names


def test_filtration_routes_clean_and_filters_polluted():
    eng = RecordingEngine()
    agent = FiltrationAgent(eng)
    clean = Droplet(id="mem_clean", phase=Phase.LIQUID)
    polluted = Droplet(id="mem_bad", phase=Phase.POLLUTED)
    agent.run(AgentContext(stage="filter", payload={"droplets": [clean, polluted]}))
    names = eng.method_names()
    assert "assess_and_route" in names  # clean droplet assessed
    assert "filter" in names  # polluted droplet filtered


def test_filtration_agent_identity_is_filtration_high_trust():
    agent = FiltrationAgent(RecordingEngine())
    ident = agent.identity()
    assert ident.is_filtration is True
    assert ident.trust_level is TrustLevel.HIGH_TRUST


def test_recall_searches_access_checks_and_ranks():
    eng = RecordingEngine()
    agent = RecallAgent(eng)
    out = agent.run(AgentContext(stage="recall", payload={"query": {"q": "x"}}))
    names = eng.method_names()
    assert names[0] == "search"
    assert names.count("check_access") == 2  # one per candidate
    assert "rank" in names
    # mem_b was denied by the engine, so only mem_a survives to ranking.
    assert [d.id for d in out] == ["mem_a"]


def test_privacy_checks_access_and_scores_risk():
    eng = RecordingEngine()
    agent = PrivacyAgent(eng)
    d = Droplet(id="mem_1")
    out = agent.run(AgentContext(stage="expose", payload={"droplets": [d]}))
    names = eng.method_names()
    assert "check_access" in names
    assert "privacy_risk" in names
    assert out[0]["privacy_risk"] == 0.42
    # privacy acts as a user proxy with the EXPOSE_TO_USER operation.
    expose_calls = [c for c in eng.calls if c[0] == "check_access"]
    assert expose_calls[0][1][3] is Operation.EXPOSE_TO_USER


def test_reflection_pulls_aged_and_reverifies():
    eng = RecordingEngine()
    agent = ReflectionAgent(eng)
    agent.run(AgentContext(stage="reflect", payload={}))
    names = eng.method_names()
    assert "aged_droplets" in names
    assert "reverify" in names


def test_distillation_clusters_then_distills():
    eng = RecordingEngine()
    agent = DistillationAgent(eng)
    out = agent.run(
        AgentContext(stage="distill", payload={"droplets": [Droplet(id="m1"), Droplet(id="m2")]})
    )
    names = eng.method_names()
    assert names[0] == "cluster"
    assert "distill" in names
    assert out[0].id == "mem_principle"


def test_archivist_dispatches_actions():
    eng = RecordingEngine()
    agent = ArchivistAgent(eng)
    freeze_me = Droplet(id="mem_f", meta={"archive_action": "freeze"})
    sink_me = Droplet(id="mem_s", meta={"archive_action": "sediment"})
    drop_me = Droplet(id="mem_d")
    agent.run(
        AgentContext(
            stage="archive",
            payload={"droplets": [freeze_me, sink_me, drop_me], "actions": {"mem_d": "delete"}},
        )
    )
    names = eng.method_names()
    assert "freeze" in names
    assert "sediment" in names
    assert "delete" in names


def test_archivist_defaults_to_sediment():
    eng = RecordingEngine()
    agent = ArchivistAgent(eng)
    agent.run(AgentContext(stage="archive", payload={"droplets": [Droplet(id="m")]}))
    assert eng.method_names() == ["sediment"]


# --- runtime / registry ------------------------------------------------------


def test_runtime_registers_and_ticks_in_order():
    eng = RecordingEngine()
    runtime = AgentRuntime()
    runtime.register(CaptureAgent(eng))
    runtime.register(HydrologistAgent(eng))
    ctx = runtime.tick("capture", AgentContext(payload={"events": ["e1"]}))
    # both capture and hydrologist handle the 'capture' stage; results recorded.
    assert "capture" in ctx.results
    assert "hydrologist" in ctx.results
    # capture ran before hydrologist (propose_droplet precedes detect_triggers).
    names = eng.method_names()
    assert names.index("propose_droplet") < names.index("detect_triggers")


def test_runtime_skips_agents_that_do_not_handle_stage():
    eng = RecordingEngine()
    runtime = AgentRuntime()
    runtime.register(CaptureAgent(eng))  # only handles 'capture'
    ctx = runtime.tick("recall", AgentContext(payload={"query": {}}))
    assert "capture" not in ctx.results
    assert eng.calls == []


def test_build_default_runtime_has_eight_roles():
    runtime = build_default_runtime(RecordingEngine())
    assert len(runtime.agents) == 8
    names = {a.name for a in runtime.agents}
    assert names == {
        "capture",
        "hydrologist",
        "filtration",
        "privacy",
        "recall",
        "reflection",
        "distillation",
        "archivist",
    }


def test_default_runtime_recall_stage_end_to_end():
    eng = RecordingEngine()
    runtime = build_default_runtime(eng)
    ctx = runtime.tick("recall", AgentContext(payload={"query": {"q": "x"}}))
    # recall + privacy both act on the recall stage.
    assert "recall" in ctx.results
    assert "privacy" in ctx.results
    # recall populated ctx.data['recalled'], privacy consumed it.
    assert [d.id for d in ctx.data["recalled"]] == ["mem_a"]
    assert isinstance(ctx.results["privacy"], list)


def test_agent_context_records_results():
    ctx = AgentContext(stage="x")
    ctx.record("foo", 123)
    assert ctx.results["foo"] == 123


def test_reservoir_import_used():
    # sanity: Reservoir import is exercised so lint doesn't flag an unused import.
    assert Reservoir.SURFACE.value == "surface"
