"""Consolidation eval (#4): does distilling a cluster of episodes into ONE
principle yield a single memory that surfaces, leads the ranking, and replaces the
raw episodes? (evals/README.md §3.)

baseline_flat = the raw episodes only (no consolidation) — recall returns fragments.
treatment     = cluster the linked episodes (activation.cluster) + distill them into
                one SACRED principle, then recall with abstraction_bonus on.

Metrics (per theme):
* principle_present_rate -- the distilled principle surfaces for the theme query.
* principle_top1_rate    -- the principle is the #1 result (vs. a raw episode);
                            baseline is 0 by construction (no principle exists).
* compression_ratio      -- episodes collapsed into a single principle (N -> 1).

The principle TEXT is supplied by the dataset, so this isolates the
consolidation+recall mechanism from the abstractor's text quality (backend-bound).
"""
from __future__ import annotations

from typing import Any

from evals.datasets import load_consolidation
from evals.harness import EvalResult, build_eval_engine
from evals.metrics import mean
from hydromemory.activation import LINK_KINDS
from hydromemory.activation import cluster as activation_cluster
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.recall import RecallWeights
from hydromemory.schema import State

# Distilled principles land in CLOUD (ADR-0036), the approved-agent abstraction
# reservoir — so an *ordinary* assistant (APPROVED, NOT a user-proxy) can recall and
# reuse them, which is exactly ADR-0031's goal. We deliberately score with that
# ordinary identity (mirroring Engine.answer's default) to prove the reuse path: it
# previously read 0.00 here because principles were SACRED (user-proxy-only).
_AGENT = AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED)
_EPISODE_STATE = dict(fluidity=0.8, purity=0.9, pressure=0.4, gravity=0.4, confidence=0.8)


def _ingest_theme(engine: Any, theme: dict) -> list[Any]:
    """Absorb a theme's episodes and chain-link them (so cluster groups them)."""
    handles = [engine.verbs.absorb(ep["content"], state=State(**_EPISODE_STATE)) for ep in theme["episodes"]]
    for left, right in zip(handles, handles[1:], strict=False):
        engine.verbs.flow(left, [right.id], kind="associations")
    return handles


def _neighbors_over(engine: Any):
    def neighbors(droplet_id: str) -> list[tuple[str, str]]:
        d = engine.repo.get(droplet_id)
        if d is None:
            return []
        return [(dst, kind) for kind in LINK_KINDS for dst in getattr(d.links, kind)]

    return neighbors


def run(
    *,
    backend: str = "local",
    k: int = 10,
    abstraction_bonus: float = 2.0,
    dataset: str | None = None,
    limit: int | None = None,
) -> list[EvalResult]:
    themes = load_consolidation(dataset)
    if limit is not None:
        themes = themes[:limit]
    results: list[EvalResult] = []
    for condition in ("baseline_flat", "treatment"):
        engine = build_eval_engine(backend=backend)
        try:
            present_flags: list[float] = []
            top1_flags: list[float] = []
            episode_counts: list[float] = []
            per_theme: list[dict] = []
            for theme in themes:
                handles = _ingest_theme(engine, theme)
                episode_counts.append(float(len(handles)))
                principle_id: str | None = None
                weights = None
                if condition == "treatment":
                    groups = activation_cluster(handles, _neighbors_over(engine))
                    group = max(groups, key=len)  # the theme's connected episodes
                    principle = engine.verbs.distill(group, principle=theme["principle"])
                    principle_id = principle.id
                    weights = RecallWeights(abstraction_bonus=abstraction_bonus)
                resp = engine.verbs.precipitate(theme["query"], agent=_AGENT, k=k, weights=weights)
                ranked_ids = [r.droplet_id for r in resp.result]
                present = bool(principle_id and principle_id in ranked_ids)
                top1 = bool(principle_id and ranked_ids and ranked_ids[0] == principle_id)
                present_flags.append(float(present))
                top1_flags.append(float(top1))
                per_theme.append({"theme": theme["id"], "present": present, "top1": top1, "episodes": len(handles)})
        finally:
            engine.close()
        n = len(themes)
        detail = {"backend": backend, "k": k, "abstraction_bonus": abstraction_bonus, "per_theme": per_theme}
        results.append(EvalResult("consolidation", condition, "principle_present_rate", round(mean(present_flags), 4), n, detail))
        results.append(EvalResult("consolidation", condition, "principle_top1_rate", round(mean(top1_flags), 4), n, detail))
        if condition == "treatment":
            results.append(EvalResult("consolidation", condition, "compression_ratio", round(mean(episode_counts), 4), n, detail))
    return results
