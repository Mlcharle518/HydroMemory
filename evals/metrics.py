"""Pure metric functions for the eval harness (no I/O)."""
from __future__ import annotations

from collections.abc import Sequence


def recall(retrieved: Sequence[str], gold: set[str]) -> float:
    """Fraction of the gold set present anywhere in ``retrieved``."""
    if not gold:
        return 0.0
    return len(set(retrieved) & gold) / len(gold)


def precision(retrieved: Sequence[str], gold: set[str]) -> float:
    """Fraction of ``retrieved`` that is in the gold set."""
    got = set(retrieved)
    if not got:
        return 0.0
    return len(got & gold) / len(got)


def recall_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """Recall over the first ``k`` retrieved ids."""
    return recall(list(retrieved)[:k], gold)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def presence_rate(pairs: Sequence[tuple[set[str], set[str]]]) -> float:
    """Fraction of (checkpoint, id) pairs where the id appears in the recalled set.

    Used both for *stale resurfacing* (ids = facts that should be gone — lower is
    better) and *retention* (ids = facts that should remain — higher is better).
    """
    total = sum(len(ids) for _got, ids in pairs)
    if total == 0:
        return 0.0
    hits = sum(1 for got, ids in pairs for i in ids if i in got)
    return hits / total
