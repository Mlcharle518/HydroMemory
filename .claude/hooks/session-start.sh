#!/bin/bash
# SessionStart hook for HydroMemory — installs the dev/test dependencies so
# that pytest, ruff, and mypy all run out of the box in Claude Code on the web.
# Skip the heavy extras (`local` = sentence-transformers, `ann` = faiss-cpu);
# their tests are gated/skipped when the imports aren't available.
set -euo pipefail

# Only run in the remote (web) environment — locally, the developer manages
# their own venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Upgrade pip when allowed (some host pythons forbid self-upgrade); ignore failure.
python -m pip install --upgrade pip 2>/dev/null || true
pip install -e ".[dev,claude,server,vault]"
