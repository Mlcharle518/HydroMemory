"""Track B: Hydro Query Language (PRD §13) -- parsing + execution.

Parses all four §13 example queries into the correct AST and runs each through
the executor against a fake repository.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from hydromemory.hql import HQLSyntaxError, compile_precipitate, execute, parse
from hydromemory.hql.ast import OutputSpec, Predicate
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Permissions, Phase, State
from hydromemory.storage.repository import DropletRepository

GET_Q = (
    'GET memories WHERE reservoir="groundwater" AND type="communication_preference" '
    'AND purity>0.8 AND permission.allows("assistant")'
)
PRECIP_Q = (
    'PRECIPITATE cloud WHERE theme="user cognitive style" '
    'AND trigger="system architecture request"'
)
FILTER_Q = 'FILTER memories WHERE phase="polluted" AND related_to="user identity"'
DISTILL_Q = 'DISTILL memories WHERE topic="AI memory" GROUP BY pattern OUTPUT principle'


# --- Parsing: all four example queries -------------------------------------
def test_parse_get_query():
    q = parse(GET_Q)
    assert q.verb == "GET"
    assert q.target == "memories"
    assert q.predicates == [
        Predicate("reservoir", "=", "groundwater"),
        Predicate("type", "=", "communication_preference"),
        Predicate("purity", ">", 0.8),
        Predicate("permission.allows", "call", "assistant"),
    ]


def test_parse_precipitate_query():
    q = parse(PRECIP_Q)
    assert q.verb == "PRECIPITATE"
    assert q.target == "cloud"
    assert q.predicates == [
        Predicate("theme", "=", "user cognitive style"),
        Predicate("trigger", "=", "system architecture request"),
    ]


def test_parse_filter_query():
    q = parse(FILTER_Q)
    assert q.verb == "FILTER"
    assert q.predicates == [
        Predicate("phase", "=", "polluted"),
        Predicate("related_to", "=", "user identity"),
    ]


def test_parse_distill_query():
    q = parse(DISTILL_Q)
    assert q.verb == "DISTILL"
    assert q.group_by == "pattern"
    assert q.output == OutputSpec("principle")
    assert q.predicates == [Predicate("topic", "=", "AI memory")]


# --- Parsing edge cases -----------------------------------------------------
def test_parse_rejects_empty():
    with pytest.raises(HQLSyntaxError):
        parse("")


def test_parse_rejects_unknown_verb():
    with pytest.raises(HQLSyntaxError):
        parse("DELETE memories WHERE phase=\"liquid\"")


def test_parse_rejects_trailing_junk():
    with pytest.raises(HQLSyntaxError):
        parse('GET memories WHERE phase="liquid" garbage')


def test_parse_rejects_missing_operator():
    with pytest.raises(HQLSyntaxError):
        parse("GET memories WHERE phase")


def test_lexer_unterminated_string():
    with pytest.raises(HQLSyntaxError):
        parse('GET memories WHERE phase="liquid')


# --- PRECIPITATE JSON op compilation ---------------------------------------
def test_precipitate_compiles_to_json_op():
    op = compile_precipitate(parse(PRECIP_Q))
    assert op == {
        "operation": "PRECIPITATE",
        "query": {
            "theme": "user cognitive style",
            "trigger": "system architecture request",
        },
        "output": {"mode": "behavioral_guidance", "include_explanation": False},
    }


def test_precipitate_json_op_with_purity_and_privacy():
    q = parse(
        'PRECIPITATE cloud WHERE theme="t" AND reservoir="groundwater" '
        'AND purity>0.8 AND maximum_privacy_risk=0.2'
    )
    op = compile_precipitate(q)
    assert op["query"]["reservoirs"] == ["groundwater"]
    assert op["query"]["minimum_purity"] == 0.8
    assert op["query"]["maximum_privacy_risk"] == 0.2


# --- Execution --------------------------------------------------------------
class _QueryRepo(DropletRepository):
    """Fake repo whose ``query`` honours the indexed filter kwargs."""

    def __init__(self, droplets: list[Droplet]) -> None:
        self._all = {d.id: d for d in droplets}

    def upsert(self, droplet: Droplet) -> None:
        self._all[droplet.id] = droplet

    def get(self, droplet_id: str) -> Droplet | None:
        return self._all.get(droplet_id)

    def delete(self, droplet_id: str) -> None:
        self._all.pop(droplet_id, None)

    def all_ids(self) -> list[str]:
        return list(self._all)

    def query(self, *, reservoir=None, phase=None, memory_type=None, min_purity=None,
              visibility=None, allowed_agent=None, usable_for_response_only=False, limit=None):
        out = []
        for d in self._all.values():
            if reservoir is not None and d.reservoir is not reservoir:
                continue
            if phase is not None and d.phase is not phase:
                continue
            if memory_type is not None and d.memory_type != memory_type:
                continue
            if min_purity is not None and d.state.purity < min_purity:
                continue
            out.append(d)
        return out

    def search_similar(self, embedding, k=10, candidate_filter=None):
        return []

    def add_link(self, src_id, kind, dst_id):
        pass

    def remove_link(self, src_id, kind, dst_id):
        pass

    def touch_cycle(self, droplet_id, **kw):
        pass

    def rebuild_index(self):
        pass

    def close(self):
        pass


def test_execute_get_applies_repo_and_post_filters():
    match = Droplet(
        id="m1",
        phase=Phase.LIQUID,
        reservoir=Reservoir.GROUNDWATER,
        memory_type="communication_preference",
        semantic_tags=["user identity"],
        state=State(purity=0.9),
        permissions=Permissions(allowed_agents=["assistant"]),
    )
    wrong_reservoir = Droplet(id="m2", reservoir=Reservoir.SURFACE, memory_type="communication_preference",
                              state=State(purity=0.9))
    low_purity = Droplet(id="m3", reservoir=Reservoir.GROUNDWATER, memory_type="communication_preference",
                         state=State(purity=0.5))
    wrong_agent = Droplet(id="m4", reservoir=Reservoir.GROUNDWATER, memory_type="communication_preference",
                          state=State(purity=0.9), permissions=Permissions(allowed_agents=["other"]))
    repo = _QueryRepo([match, wrong_reservoir, low_purity, wrong_agent])

    results = execute(parse(GET_Q), repo)
    ids = {d.id for d in results}
    assert ids == {"m1"}


def test_execute_get_strict_purity_excludes_equal():
    eq = Droplet(id="eq", reservoir=Reservoir.GROUNDWATER, memory_type="communication_preference",
                 state=State(purity=0.8), permissions=Permissions(allowed_agents=["assistant"]))
    repo = _QueryRepo([eq])
    # purity > 0.8 must exclude exactly-0.8
    assert execute(parse(GET_Q), repo) == []


def test_execute_precipitate_without_recall_returns_json_op():
    repo = _QueryRepo([])
    result = execute(parse(PRECIP_Q), repo)
    assert result["operation"] == "PRECIPITATE"


def test_execute_precipitate_with_recall_callable():
    repo = _QueryRepo([])
    captured = {}

    def recall(op: dict[str, Any]) -> str:
        captured.update(op)
        return "recall-ran"

    assert execute(parse(PRECIP_Q), repo, recall=recall) == "recall-ran"
    assert captured["operation"] == "PRECIPITATE"


def test_execute_filter_delegates_to_verbs():
    polluted = Droplet(id="p1", phase=Phase.POLLUTED, semantic_tags=["user identity"])
    repo = _QueryRepo([polluted])
    filtered: list[str] = []

    def fake_filter(d: Droplet) -> Droplet:
        filtered.append(d.id)
        d.phase = Phase.FILTERED
        return d

    verbs = types.SimpleNamespace(filter=fake_filter)
    out = execute(parse(FILTER_Q), repo, verbs=verbs)
    assert filtered == ["p1"]
    assert out[0].phase is Phase.FILTERED


def test_execute_distill_groups_and_distills():
    d1 = Droplet(id="d1", content="a", semantic_tags=["AI memory"])
    d2 = Droplet(id="d2", content="b", semantic_tags=["AI memory"])
    repo = _QueryRepo([d1, d2])
    seen: list[list[str]] = []

    def fake_distill(cluster):
        seen.append([d.id for d in cluster])
        return Droplet(id="principle", reservoir=Reservoir.SACRED)

    verbs = types.SimpleNamespace(distill=fake_distill)
    out = execute(parse(DISTILL_Q), repo, verbs=verbs)
    assert out.id == "principle"
    assert sorted(seen[0]) == ["d1", "d2"]


def test_execute_filter_requires_verbs():
    repo = _QueryRepo([])
    with pytest.raises(HQLSyntaxError):
        execute(parse(FILTER_Q), repo)
