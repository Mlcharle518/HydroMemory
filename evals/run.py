"""CLI: run an efficacy eval and print/emit a report.

    python -m evals.run --eval multihop --backend local [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from evals import (
    consolidation,
    intent_distillation,
    longmemeval,
    multihop,
    multihop_qa,
    packing,
    pollution,
    reintegration,
    scale,
)
from evals.harness import EvalResult

_EVALS = {
    "consolidation": consolidation.run,
    "intent_distillation": intent_distillation.run,
    "longmemeval": longmemeval.run,
    "multihop": multihop.run,
    "multihop_qa": multihop_qa.run,
    "packing": packing.run,
    "pollution": pollution.run,
    "reintegration": reintegration.run,
    "scale": scale.run,
}


def _print_report(name: str, backend: str, results: list[EvalResult]) -> None:
    print(f"\n== {name} (backend={backend}) ==")
    print(f"{'condition':<18} {'metric':<24} {'value':>8} {'n':>4}")
    print("-" * 58)
    for r in results:
        print(f"{r.condition:<18} {r.metric:<24} {r.value:>8.4f} {r.n:>4}")
    # Per-metric baseline -> treatment delta (condition names sort baseline_* first).
    by_metric: dict[str, dict[str, float]] = {}
    for r in results:
        by_metric.setdefault(r.metric, {})[r.condition] = r.value
    print()
    for metric, conds in by_metric.items():
        if len(conds) == 2:
            (c_lo, v_lo), (c_hi, v_hi) = sorted(conds.items())
            print(f"{metric:<24} {c_lo} {v_lo:.4f} -> {c_hi} {v_hi:.4f}  (delta {v_hi - v_lo:+.4f})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run")
    parser.add_argument("--eval", choices=sorted(_EVALS), default="multihop")
    parser.add_argument("--backend", default="local", help="embedding backend: local | stub")
    parser.add_argument("--json", action="store_true", help="also write a JSON report to evals/results/")
    parser.add_argument("--dataset", default=None, help="path to a dataset file (eval-specific)")
    parser.add_argument("--limit", type=int, default=None, help="cap instances/cases (eval-specific)")
    args = parser.parse_args(argv)

    results = _EVALS[args.eval](backend=args.backend, dataset=args.dataset, limit=args.limit)
    _print_report(args.eval, args.backend, results)

    if args.json:
        out_dir = Path(__file__).parent / "results"
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_path = out_dir / f"{args.eval}-{args.backend}-{stamp}.json"
        out_path.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
        print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
