"""Multi-hop retrieval eval (#2): does spreading activation surface the supporting
constellation that plain cosine recall misses? (evals/README.md §3.)

Baseline   = precipitate(traverse=False)            -- the RAG baseline.
Treatment  = precipitate(traverse=True, bonus>0)    -- the spreading-activation spine.

Metric = support_recall: fraction of each question's gold supporting droplets that
surface in the returned set (precision + mean result size reported as the tradeoff).
This measures *retrieval*, not answer accuracy (there is no LLM reader yet).
"""
from __future__ import annotations

from evals.datasets import load_multihop
from evals.harness import EvalResult, build_eval_engine, ingest_corpus
from evals.metrics import mean, precision, recall
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.recall import RecallWeights

_AGENT = AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED)


def run(
    *,
    backend: str = "local",
    k: int = 5,
    activation_bonus: float = 1.0,
    dataset: str | None = None,
) -> list[EvalResult]:
    data = load_multihop(dataset)
    engine = build_eval_engine(backend=backend)
    try:
        id_map = ingest_corpus(engine, data.corpus)
        conditions = {
            "baseline_cosine": {"traverse": False, "weights": None},
            "traverse": {"traverse": True, "weights": RecallWeights(activation_bonus=activation_bonus)},
        }
        results: list[EvalResult] = []
        for condition, opts in conditions.items():
            recalls: list[float] = []
            precisions: list[float] = []
            sizes: list[float] = []
            per_question: list[dict] = []
            for q in data.questions:
                gold = {id_map[g] for g in q.gold_support if g in id_map}
                resp = engine.verbs.precipitate(q.seed, agent=_AGENT, k=k, **opts)
                got = [r.droplet_id for r in resp.result]
                rec, prec = recall(got, gold), precision(got, gold)
                recalls.append(rec)
                precisions.append(prec)
                sizes.append(float(len(got)))
                per_question.append(
                    {"q": q.id, "recall": round(rec, 3), "precision": round(prec, 3), "n_results": len(got)}
                )
            n = len(data.questions)
            detail = {
                "backend": backend,
                "k": k,
                "activation_bonus": activation_bonus,
                "mean_result_size": round(mean(sizes), 3),
                "per_question": per_question,
            }
            results.append(EvalResult("multihop", condition, "support_recall", round(mean(recalls), 4), n, detail))
            results.append(EvalResult("multihop", condition, "precision", round(mean(precisions), 4), n, detail))
        return results
    finally:
        engine.close()
