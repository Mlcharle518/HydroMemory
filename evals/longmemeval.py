"""LongMemEval adapter (the external credibility milestone, README §6 Phase 4).

Wraps HydroMemory as a LongMemEval-compatible memory + QA system: ingest a question's
multi-session conversation history as droplets (turns chained within a session), recall
+ reader-answer (`Engine.answer`, ADR-0035), then LLM-judge the answer against the gold;
report accuracy overall and by `question_type`.

Default dataset is a small SYNTHETIC sample in LongMemEval's schema (proves the adapter
end-to-end without the multi-hundred-MB real download). Run the real benchmark with
`--dataset path/to/longmemeval_s.json` (a JSON list of instances).

LLM-in-the-loop: needs `ANTHROPIC_API_KEY`; ~2 calls/question (answer + judge). Skips
with a note + no call otherwise. Cost scales with instances × history length — use
`--limit` and a cheaper `HYDRO_CLAUDE_MODEL` for the real set.
"""
from __future__ import annotations

import os
import random
from collections import defaultdict
from typing import Any

from evals.datasets import load_longmemeval
from evals.harness import EvalResult, build_eval_engine
from evals.metrics import mean
from hydromemory.config import HydroConfig
from hydromemory.reader import build_composer
from hydromemory.recall import RecallWeights
from hydromemory.schema import State

_TURN_STATE = dict(fluidity=0.7, purity=0.9, pressure=0.3, gravity=0.3, confidence=0.8)


def _has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _composer() -> Any:
    cfg = HydroConfig.from_env()
    cfg.intelligence_backend = "claude"  # select the LLM composer
    return build_composer(cfg)


def _judge_client() -> Any:
    from hydromemory.intelligence.claude_backend import _ClaudeClient

    return _ClaudeClient(HydroConfig.from_env())


def _ingest_history(engine: Any, sessions: list[list[dict]]) -> None:
    """Absorb each turn as a droplet; chain consecutive turns within a session."""
    for session in sessions:
        previous = None
        for turn in session:
            content = f"[{turn.get('role', 'user')}] {turn.get('content', '')}".strip()
            if not content:
                continue
            droplet = engine.verbs.absorb(content, state=State(**_TURN_STATE))
            if previous is not None:
                engine.verbs.flow(previous, [droplet.id], kind="associations")
            previous = droplet


def _judge(client: Any, question: str, gold: str, answer: str) -> bool:
    system = (
        "You grade a model's answer against a gold answer. Reply with exactly 'yes' if "
        "the model answer conveys the gold answer, otherwise 'no'."
    )
    user = f"Question: {question}\nGold answer: {gold}\nModel answer: {answer}\nCorrect (yes/no)?"
    return client.complete_text(system, user, max_tokens=4).strip().lower().startswith("y")


def run(
    *,
    backend: str = "local",
    dataset: str | None = None,
    limit: int | None = None,
    k: int = 12,
    seed: int = 0,
    **_: Any,
) -> list[EvalResult]:
    if not _has_key():
        return [
            EvalResult("longmemeval", "hydromemory", "available", 0.0, 0,
                       {"note": "needs ANTHROPIC_API_KEY (LLM-in-the-loop); set it and re-run"})
        ]
    instances = load_longmemeval(dataset)
    if limit:
        # The dataset is ordered by question_type, so shuffle (seeded) before
        # capping — otherwise a small --limit samples a single (hard) category.
        instances = list(instances)
        random.Random(seed).shuffle(instances)
        instances = instances[: int(limit)]
    composer = _composer()
    judge = _judge_client()

    overall: list[float] = []
    by_type: dict[str, list[float]] = defaultdict(list)
    calls = 0
    for inst in instances:
        engine = build_eval_engine(backend=backend)
        try:
            _ingest_history(engine, inst.get("haystack_sessions", []))
            result = engine.answer(
                inst["question"], k=k, traverse=True, weights=RecallWeights(activation_bonus=1.0), composer=composer
            )
            correct = _judge(judge, inst["question"], inst.get("answer", ""), result.answer)
            calls += 2
        finally:
            engine.close()
        score = 1.0 if correct else 0.0
        overall.append(score)
        by_type[inst.get("question_type", "unknown")].append(score)

    detail = {
        "backend": backend,
        "k": k,
        "model": os.environ.get("HYDRO_CLAUDE_MODEL", "claude-opus-4-7"),
        "calls": calls,
        "instances": len(instances),
        "dataset": dataset or "synthetic sample",
    }
    results = [EvalResult("longmemeval", "hydromemory", "accuracy", round(mean(overall), 4), len(overall), detail)]
    for qtype, scores in sorted(by_type.items()):
        results.append(EvalResult("longmemeval", qtype, "accuracy_by_type", round(mean(scores), 4), len(scores), detail))
    return results
