"""Multi-hop QA eval (#2, end-to-end): does spreading-activation traversal improve the
ANSWER, not just retrieval? Drives the *promoted* reader (`Engine.answer` +
`reader.compose_answer`, ADR-0035): recall (traverse off vs on) → an LLM reader composes
an answer from the constellation → score `answer_key_coverage` (the chained-fact 2nd/3rd-
hop terms that surface in the answer). (README §3.)

LLM-in-the-loop: needs `ANTHROPIC_API_KEY` (skips + makes no call otherwise).
"""
from __future__ import annotations

import os
from typing import Any

from evals.datasets import load_multihop
from evals.harness import EvalResult, build_eval_engine, ingest_corpus
from evals.metrics import mean
from hydromemory.config import HydroConfig
from hydromemory.reader import build_composer
from hydromemory.recall import RecallWeights


def _has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _claude_composer() -> Any:
    cfg = HydroConfig.from_env()  # ANTHROPIC_API_KEY + HYDRO_CLAUDE_MODEL
    cfg.intelligence_backend = "claude"  # select the LLM composer in build_composer
    return build_composer(cfg)


def _coverage(answer: str, keys: list[str]) -> float:
    if not keys:
        return 0.0
    lower = answer.lower()
    return sum(1 for key in keys if key.lower() in lower) / len(keys)


def run(*, backend: str = "local", k: int = 10, activation_bonus: float = 1.0, **_: Any) -> list[EvalResult]:
    if not _has_key():
        return [
            EvalResult("multihop_qa", "llm", "available", 0.0, 0,
                       {"note": "needs ANTHROPIC_API_KEY (LLM reader); set it and re-run"})
        ]
    data = load_multihop()
    composer = _claude_composer()
    calls = 0
    results: list[EvalResult] = []
    for condition, traverse in (("baseline_cosine", False), ("traverse", True)):
        engine = build_eval_engine(backend=backend)
        try:
            ingest_corpus(engine, data.corpus)
            weights = RecallWeights(activation_bonus=activation_bonus) if traverse else None
            coverages: list[float] = []
            for q in data.questions:
                # Engine.answer = the promoted reader: recall(traverse) + compose with citations.
                result = engine.answer(q.seed, k=k, traverse=traverse, weights=weights, composer=composer)
                calls += 1
                coverages.append(_coverage(result.answer, q.answer_keys))
        finally:
            engine.close()
        detail = {
            "backend": backend,
            "k": k,
            "model": os.environ.get("HYDRO_CLAUDE_MODEL", "claude-opus-4-7"),
            "calls_so_far": calls,
        }
        results.append(
            EvalResult("multihop_qa", condition, "answer_key_coverage", round(mean(coverages), 4), len(data.questions), detail)
        )
    return results
