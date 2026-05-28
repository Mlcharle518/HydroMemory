"""Pollution / forgetting eval (#3): once a fact is corrected, does it stop
resurfacing — *without* over-forgetting rare-but-true facts? (evals/README.md §3.)

baseline_flat = a naive store: absorb + recall, no contamination routing, no decay.
treatment     = the HydroMemory lifecycle: on a correction, mark the old fact
                polluted + link the contradiction; decay salience over cycles.

Metrics (over the timeline's checkpoints):
* stale_resurfacing_rate -- a corrected-away fact still appears (LOWER is better).
* rare_true_retention    -- a low-salience true fact still appears (HIGHER is better;
                            guards against decay over-forgetting).
* correct_answer_present -- the corrected replacement appears (sanity).
"""
from __future__ import annotations

from typing import Any

from evals.datasets import load_pollution
from evals.harness import EvalResult, build_eval_engine
from evals.metrics import presence_rate
from hydromemory import forgetting
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.schema import State

_AGENT = AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED)


def _state_for(op: dict) -> State:
    if op.get("rare_true"):  # rare-but-true: quiet (low salience) yet high purity
        return State(purity=0.95, pressure=0.1, gravity=0.2, fluidity=0.1, confidence=0.8)
    return State(purity=0.9, pressure=0.5, gravity=0.4, fluidity=0.3, confidence=0.8)


def _run_timeline(engine: Any, timeline: list[dict], *, condition: str, k: int) -> list[dict]:
    id_map: dict[str, str] = {}
    handles: dict[str, Any] = {}
    rare_true: set[str] = set()
    checkpoints: list[dict] = []

    for op in timeline:
        kind = op["op"]
        if kind == "absorb":
            d = engine.verbs.absorb(op["content"], state=_state_for(op))
            id_map[op["id"]] = d.id
            handles[op["id"]] = d
            if op.get("rare_true"):
                rare_true.add(op["id"])
        elif kind == "correct":
            new = engine.verbs.absorb(op["with"], state=_state_for(op))
            id_map[op["new_id"]] = new.id
            handles[op["new_id"]] = new
            if condition == "treatment":
                # Supersede the old fact: link the contradiction + route it to the
                # contaminated pool (phase POLLUTED -> accessibility 0, threshold 1.25).
                old = handles[op["target"]]
                engine.verbs.flow(old, [new.id], kind="contradictions")
                engine.verbs.pollute(engine.repo.get(old.id) or old, "superseded by a later correction")
            # baseline_flat: do nothing -> the stale fact stays fully usable.
        elif kind == "decay":
            if condition == "treatment":
                cycles = int(op.get("cycles", 1))
                for real in id_map.values():
                    d = engine.repo.get(real)
                    if d is not None:
                        engine.repo.upsert(forgetting.decay(d, idle_cycles=cycles))
            # baseline_flat: no decay.
        elif kind == "checkpoint":
            resp = engine.verbs.precipitate(op["probe"], agent=_AGENT, k=k)
            got = {r.droplet_id for r in resp.result}
            absent = {id_map[i] for i in op.get("expect_absent", []) if i in id_map}
            present = {id_map[i] for i in op.get("expect_present", []) if i in id_map}
            rare = {id_map[i] for i in op.get("expect_present", []) if i in rare_true and i in id_map}
            correct = present - rare  # corrected replacements (non-rare expected facts)
            checkpoints.append({"id": op.get("id", "cp"), "got": got, "absent": absent, "rare": rare, "correct": correct})
    return checkpoints


def run(*, backend: str = "local", k: int = 10, dataset: str | None = None) -> list[EvalResult]:
    timeline = load_pollution(dataset)
    results: list[EvalResult] = []
    for condition in ("baseline_flat", "treatment"):
        engine = build_eval_engine(backend=backend)
        try:
            cps = _run_timeline(engine, timeline, condition=condition, k=k)
        finally:
            engine.close()
        n = len(cps)
        detail = {"backend": backend, "k": k, "checkpoints": [c["id"] for c in cps]}
        stale = presence_rate([(c["got"], c["absent"]) for c in cps])
        retain = presence_rate([(c["got"], c["rare"]) for c in cps])
        correct = presence_rate([(c["got"], c["correct"]) for c in cps])
        results.append(EvalResult("pollution", condition, "stale_resurfacing_rate", round(stale, 4), n, detail))
        results.append(EvalResult("pollution", condition, "rare_true_retention", round(retain, 4), n, detail))
        results.append(EvalResult("pollution", condition, "correct_answer_present", round(correct, 4), n, detail))
    return results
