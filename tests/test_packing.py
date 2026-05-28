"""Working-set packing (ADR-0033): budget, abstraction preference, provenance
dedup, and primacy/recency placement over precipitate's RecallResult list.
"""
from __future__ import annotations

from collections.abc import Callable

from hydromemory.packing import pack_working_set
from hydromemory.recall import RecallMode, RecallResult
from hydromemory.schema import Droplet, Phase


def _r(
    did: str,
    score: float,
    *,
    text: str = "",
    show: bool = True,
    mode: RecallMode = RecallMode.LITERAL,
) -> RecallResult:
    return RecallResult(
        mode=mode,
        surface_text=text,
        internal_guidance="",
        show_to_user=show,
        explanation="",
        droplet_id=did,
        score=score,
    )


def _getter(droplets: list[Droplet]) -> Callable[[str], Droplet | None]:
    by_id = {d.id: d for d in droplets}
    return lambda did: by_id.get(did)


def test_no_budget_is_passthrough():
    results = [_r("a", 0.9, text="x"), _r("b", 0.5, text="y")]
    assert pack_working_set(results) == results  # score order, unchanged


def test_budget_trims_to_fit():
    results = [_r("a", 0.9, text="x" * 10), _r("b", 0.8, text="y" * 10), _r("c", 0.7, text="z" * 10)]
    out = pack_working_set(results, token_budget=25)
    assert len(out) == 2
    assert sum(len(r.surface_text) for r in out if r.show_to_user) <= 25
    assert {r.droplet_id for r in out} == {"a", "b"}  # the two highest-score fit


def test_abstracted_preferred_under_tight_budget():
    lit = _r("lit", 0.9, text="AAAA", mode=RecallMode.LITERAL)
    pat = _r("pat", 0.5, text="BBBB", mode=RecallMode.PATTERN)
    out = pack_working_set([lit, pat], token_budget=4)  # only one 4-char item fits
    assert [r.droplet_id for r in out] == ["pat"]  # abstracted kept despite lower score


def test_provenance_dedup_drops_source_of_present_principle():
    principle = Droplet(id="p", phase=Phase.GROUNDWATER, source="distill")
    principle.links.derived_from = ["s"]
    source = Droplet(id="s")
    results = [_r("p", 0.8, text="P"), _r("s", 0.9, text="S")]
    out = pack_working_set(results, token_budget=100, get_droplet=_getter([principle, source]))
    assert {r.droplet_id for r in out} == {"p"}  # redundant source dropped, principle kept


def test_primacy_recency_places_best_at_edges():
    results = [_r(c, s, text="x") for c, s in [("a", 0.5), ("b", 0.4), ("c", 0.3), ("d", 0.2), ("e", 0.1)]]
    out = pack_working_set(results, token_budget=1000)
    assert out[0].droplet_id == "a"   # best at the front
    assert out[-1].droplet_id == "b"  # 2nd best at the back
    assert out[2].droplet_id == "e"   # weakest in the middle


def test_internal_items_consume_no_budget_and_are_retained():
    surfaced = _r("vis", 0.9, text="x" * 100, show=True)
    silent = _r("sil", 0.8, text="", show=False)
    behavioral = _r("beh", 0.7, text="", show=False, mode=RecallMode.BEHAVIORAL)
    out = pack_working_set([surfaced, silent, behavioral], token_budget=100)
    assert {r.droplet_id for r in out} == {"vis", "sil", "beh"}  # zero-cost guidance retained
