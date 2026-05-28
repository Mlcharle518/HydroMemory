"""Working-set packing (ADR-0033): turn ranked recall into a budgeted, ordered
context block that counters "lost in the middle".

Selective recall (`precipitate`) decides *what* enters the window; this is the
*assembly* step that decides how much of it, and *where* it lands. It is a pure
function over `precipitate`'s `RecallResult` list and **does not touch scoring or
thresholds** — recall still decides membership; the packer only trims and orders
what recall already passed. The default (no `token_budget`) is a passthrough, so
v1 behavior is byte-identical.

Three behaviors (only with a budget):

* **Provenance dedup** — when a source droplet *and* its distilled principle both
  cleared recall, keep the principle and drop the redundant source (via the
  principle's ``links.derived_from``). Best-effort: bounded by link quality, and
  only when a ``get_droplet`` accessor is supplied.
* **Abstraction preference** — spend the budget on abstracted/principle droplets
  first (``RecallMode.PATTERN``, or a vapor/cloud/groundwater / distilled droplet)
  so each token buys more meaning. Composes with ``abstraction_bonus`` (ADR-0026).
* **Primacy/recency placement** — put the highest-value items at the *start and
  end* of the block and the weakest in the middle, directly countering the model's
  center-of-context weakness.

The tokenizer is pluggable (character-count default, offline); the budget is a
soft guard, not an exact model-token count until a real tokenizer is injected.
This cannot fix attention rot *inside* the model — it only shapes how much you
place in the middle and how much you rely on it.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from hydromemory.recall import RecallMode, RecallResult
from hydromemory.schema import Droplet, Phase

GetDroplet = Callable[[str], "Droplet | None"]
Tokenizer = Callable[[str], int]

# Phases whose content is already an abstraction/derived pattern.
_ABSTRACTION_PHASES: frozenset[Phase] = frozenset({Phase.VAPOR, Phase.CLOUD, Phase.GROUNDWATER})


def _char_tokens(text: str) -> int:
    """Default offline tokenizer: a character count (approximate)."""
    return len(text)


def _is_abstracted(result: RecallResult, droplet: Droplet | None) -> bool:
    """Whether this result is an abstracted/principle memory (budget-preferred)."""
    if result.mode is RecallMode.PATTERN:
        return True
    if droplet is None:
        return False
    if droplet.phase in _ABSTRACTION_PHASES:
        return True
    return bool(droplet.meta.get("principle")) or droplet.source == "distill"


def _primacy_recency_order(items: list[RecallResult]) -> list[RecallResult]:
    """Reorder a score-desc list so the best land at the edges, weakest in the middle.

    items[0] (best) -> front, items[1] -> back, items[2] -> front+1, ... so the two
    strongest sit at positions 0 and N-1 and the weakest in the center.
    """
    n = len(items)
    out: list[RecallResult | None] = [None] * n
    left, right = 0, n - 1
    for i, item in enumerate(items):
        if i % 2 == 0:
            out[left] = item
            left += 1
        else:
            out[right] = item
            right -= 1
    return [x for x in out if x is not None]


def pack_working_set(
    results: Sequence[RecallResult],
    *,
    token_budget: int | None = None,
    get_droplet: GetDroplet | None = None,
    tokenizer: Tokenizer | None = None,
) -> list[RecallResult]:
    """Pack ranked recall results into a budgeted, primacy/recency-ordered set.

    ``token_budget=None`` (the default) is a **passthrough** — the score-ordered
    list is returned unchanged. With a budget, the budget is charged only against
    *surfaced* text (``show_to_user``); internal-only guidance (SILENT/BEHAVIORAL)
    is retained at zero surface cost. See the module docstring for the three
    behaviors. Pure: ``results`` are not mutated.
    """
    items = list(results)
    if token_budget is None:
        return items  # passthrough: recall's score order, unchanged

    tok = tokenizer or _char_tokens

    # 1. Provenance dedup: drop a source whose distilled principle is also present.
    if get_droplet is not None:
        present_ids = {r.droplet_id for r in items}
        superseded: set[str] = set()
        for result in items:
            droplet = get_droplet(result.droplet_id)
            if droplet is None:
                continue
            for source_id in droplet.links.derived_from:
                if source_id in present_ids:
                    superseded.add(source_id)
        items = [r for r in items if r.droplet_id not in superseded]

    # 2. Greedy fill under budget, preferring abstracted/principle droplets, then score.
    def sort_key(result: RecallResult) -> tuple[int, float]:
        droplet = get_droplet(result.droplet_id) if get_droplet is not None else None
        return (0 if _is_abstracted(result, droplet) else 1, -result.score)

    kept: list[RecallResult] = []
    spent = 0
    for result in sorted(items, key=sort_key):
        cost = tok(result.surface_text) if result.show_to_user else 0
        if cost and spent + cost > token_budget:
            continue  # would overflow the surfaced block; skip and try smaller items
        kept.append(result)
        spent += cost

    # 3. Re-rank by score, then place the best at the edges (primacy/recency).
    kept.sort(key=lambda r: r.score, reverse=True)
    return _primacy_recency_order(kept)


__all__ = ["pack_working_set", "GetDroplet", "Tokenizer"]
