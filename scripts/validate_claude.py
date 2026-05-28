r"""Manual validation of the Claude intelligence backend (requires a real key).

The offline test suite cannot exercise this path, so run it yourself:

    # PowerShell
    $env:ANTHROPIC_API_KEY="sk-ant-..."          # pip install -e ".[claude]" first
    .\.venv\Scripts\python.exe scripts\validate_claude.py

It runs EVAPORATE (abstraction), classification, and §10.1 contamination over a
few sample memories and prints the results so you can eyeball quality. Choose the
model with HYDRO_CLAUDE_MODEL (default claude-opus-4-7).
"""
from __future__ import annotations

import os
import sys

from hydromemory.config import HydroConfig
from hydromemory.intelligence import build_intelligence
from hydromemory.schema import Droplet

SAMPLES = [
    "User prefers architectural systems thinking over shallow summaries.",
    "I was dismissed during a meeting and felt ignored in front of everyone.",
    "A stranger online insisted the earth is flat and said I should just trust them.",
]


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY (and `pip install -e \".[claude]\"`) to run this.", file=sys.stderr)
        return 2

    cfg = HydroConfig.from_env()  # reads ANTHROPIC_API_KEY + HYDRO_CLAUDE_MODEL from env
    cfg.intelligence_backend = "claude"  # force the Claude text-ops path
    intel = build_intelligence(cfg)
    print(f"model={cfg.claude_model}  text-ops=claude  embedder={type(intel.embedder).__name__}")

    for sample in SAMPLES:
        print("\n---", sample)
        print("  evaporate:   ", intel.abstractor.evaporate(sample))
        c = intel.classifier.classify(sample)
        print(
            f"  classify:     type={c.memory_type} importance={c.importance:.2f} "
            f"sensitivity={c.sensitivity:.2f} lifespan={c.expected_lifespan}"
        )
        v = intel.detector.assess(Droplet(id="sample", content=sample), {})
        print(f"  contamination: contaminated={v.contaminated} confidence={v.confidence:.2f} reason={v.reason!r}")

    print("\nVALIDATE_CLAUDE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
