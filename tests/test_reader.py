"""Reader (ADR-0035): compose_answer + Engine.answer, offline (extractive composer)."""
from __future__ import annotations

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.reader import ReaderResult, compose_answer
from hydromemory.schema import Droplet, State


def _d(did: str, content: str) -> Droplet:
    return Droplet(id=did, content=content)


# --- compose_answer (pure) --------------------------------------------------
def test_compose_answer_extractive_default():
    out = compose_answer("q", [_d("a", "alpha fact"), _d("b", "beta fact")])
    assert isinstance(out, ReaderResult)
    assert "alpha fact" in out.answer
    assert out.citations == ["a"]  # extractive default surfaces + cites the top item
    assert out.context_ids == ["a", "b"]


def test_compose_answer_empty_is_graceful():
    out = compose_answer("q", [])
    assert out.citations == [] and out.context_ids == []
    assert "enough information" in out.answer.lower()


def test_compose_answer_maps_citation_markers_to_ids():
    def composer(query: str, items: list[str]) -> str:
        return "Because [1] and [3] establish it."

    out = compose_answer("q", [_d("a", "x"), _d("b", "y"), _d("c", "z")], composer=composer)
    assert out.citations == ["a", "c"]  # [1]->a, [3]->c


def test_compose_answer_ignores_out_of_range_citations():
    def composer(query: str, items: list[str]) -> str:
        return "see [1] and [99]"

    out = compose_answer("q", [_d("a", "x")], composer=composer)
    assert out.citations == ["a"]  # [99] is clamped out


# --- Engine.answer (stub backend, offline) ----------------------------------
def _engine(tmp_path):
    return build_engine(HydroConfig(db_path=str(tmp_path / "r.db"), vector_dim=64, intelligence_backend="stub"))


def test_engine_answer_default_extractive(tmp_path):
    eng = _engine(tmp_path)
    try:
        eng.verbs.absorb("The deploy command is shipit --prod.", state=State(purity=0.9, fluidity=0.6, pressure=0.4))
        eng.verbs.absorb("Lunch is served at noon.", state=State(purity=0.9, fluidity=0.6))
        out = eng.answer("how do I deploy to prod", traverse=False)  # default extractive composer
        assert out.context_ids  # recall surfaced something
        assert out.citations and out.citations[0] == out.context_ids[0]
    finally:
        eng.close()


def test_engine_answer_passes_recalled_context_to_composer(tmp_path):
    eng = _engine(tmp_path)
    try:
        eng.verbs.absorb("The deploy command is shipit --prod.", state=State(purity=0.9, fluidity=0.6, pressure=0.4))
        eng.verbs.absorb("Lunch is served at noon.", state=State(purity=0.9, fluidity=0.6))
        seen: dict[str, list[str]] = {}

        def composer(query: str, items: list[str]) -> str:
            seen["items"] = items
            return f"{items[0]} [1]"

        out = eng.answer("how do I deploy to prod", traverse=False, composer=composer)
        assert any("shipit" in item for item in seen["items"])  # the deploy fact reached the reader
        assert out.citations[0] == out.context_ids[0]
    finally:
        eng.close()
