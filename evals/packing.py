"""Packing eval (#1, lost-in-the-middle): does a real model answer better when the
answer fact sits at an EDGE of a LONG context vs buried in the MIDDLE? (README §3.)

This tests the premise behind `pack_working_set`'s primacy/recency placement — that a
model under-attends to the center of a long block, so the highest-value items belong
at the edges. We bury one needle in a long haystack of filler (default ~150 items /
several thousand tokens — a short context can't surface the effect) and sweep its
position across 0/25/50/75/100%, comparing a real model's answer accuracy.

LLM-in-the-loop: needs `ANTHROPIC_API_KEY` (skips with a note + no API call otherwise).
Pins the model via `HYDRO_CLAUDE_MODEL` and reports the call count (= cases × 5).
"""
from __future__ import annotations

import os
from typing import Any

from evals.datasets import load_packing
from evals.harness import EvalResult
from evals.metrics import mean

_SYSTEM = (
    "Answer the question using ONLY the numbered context items. "
    "Reply with just the answer in as few words as possible."
)

_FILLER_TEMPLATES = (
    "System note {i}: during a routine maintenance window the {svc} service was patched and restarted with no customer-facing impact.",
    "Status update {i}: the {svc} dashboard reported nominal metrics across every region throughout the day.",
    "Changelog {i}: a minor dependency in the {svc} pipeline was bumped; no behavioral change was expected or observed.",
    "Ops log {i}: scheduled backups for the {svc} datastore completed successfully within the usual window.",
)
_SVCS = ("billing", "search", "ingestion", "notifications", "analytics", "gateway", "scheduler", "audit")
_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


def _has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client() -> Any:
    from hydromemory.config import HydroConfig
    from hydromemory.intelligence.claude_backend import _ClaudeClient

    return _ClaudeClient(HydroConfig.from_env())  # reads ANTHROPIC_API_KEY + HYDRO_CLAUDE_MODEL


def _filler_pool(base: list[str], n: int) -> list[str]:
    """Pad the curated fillers up to `n` with distinct generated filler lines."""
    pool = list(base)
    i = 0
    while len(pool) < n:
        pool.append(_FILLER_TEMPLATES[i % len(_FILLER_TEMPLATES)].format(i=i, svc=_SVCS[i % len(_SVCS)]))
        i += 1
    return pool[:n]


def _assemble(fillers: list[str], needle: str, position: int) -> str:
    block = list(fillers)
    block.insert(position, needle)
    return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(block))


def _correct(answer: str, expected: str) -> bool:
    return expected.lower() in answer.lower()


def run(*, backend: str = "local", n_fillers: int = 150, **_: Any) -> list[EvalResult]:
    if not _has_key():
        return [
            EvalResult("packing", "llm", "available", 0.0, 0,
                       {"note": "needs ANTHROPIC_API_KEY (LLM-in-the-loop); set it and re-run"})
        ]
    data = load_packing()
    fillers = _filler_pool(data["fillers"], n_fillers)
    cases = data["cases"]
    k = len(fillers) + 1  # context items with the needle inserted
    client = _client()

    by_fraction: dict[float, list[float]] = {f: [] for f in _FRACTIONS}
    calls = 0
    for case in cases:
        for fraction in _FRACTIONS:
            position = min(k - 1, round(fraction * (k - 1)))
            block = _assemble(fillers, case["needle"], position)
            user = f"Context:\n{block}\n\nQuestion: {case['question']}\nAnswer:"
            answer = client.complete_text(_SYSTEM, user, max_tokens=32)
            calls += 1
            by_fraction[fraction].append(1.0 if _correct(answer, case["answer"]) else 0.0)

    detail = {
        "model": os.environ.get("HYDRO_CLAUDE_MODEL", "claude-opus-4-7"),
        "calls": calls,
        "cases": len(cases),
        "context_items": k,
        "n_fillers": n_fillers,
    }
    results: list[EvalResult] = []
    for fraction in _FRACTIONS:
        scores = by_fraction[fraction]
        results.append(
            EvalResult("packing", f"pos_{int(fraction * 100):03d}pct", "answer_accuracy", round(mean(scores), 4), len(scores), detail)
        )
    # Edge (0% + 100%) vs middle (50%) — the headline zone comparison.
    edge = by_fraction[0.0] + by_fraction[1.0]
    middle = by_fraction[0.5]
    results.append(EvalResult("packing", "edge", "zone_accuracy", round(mean(edge), 4), len(edge), detail))
    results.append(EvalResult("packing", "middle", "zone_accuracy", round(mean(middle), 4), len(middle), detail))
    return results
