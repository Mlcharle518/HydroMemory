# Dependency licenses

A license audit of the project's declared dependencies, for assessing public-release and
commercial-distribution readiness. Verified from installed package metadata on 2026-05-27
(versions reflect the local `.venv`; **re-run before any release** — see below).

## Bottom line

**Every dependency is permissively licensed (MIT / BSD-3-Clause / Apache-2.0 / 0BSD / Zlib).
There is no copyleft (GPL / LGPL / AGPL) anywhere in the set.** You are free to license your
own code however you choose — including proprietary or commercial — without any
dependency-imposed obligation to open-source it. The only redistribution obligation is the
usual permissive one: preserve the upstream copyright/license notices (and the `NOTICE`
files of Apache-2.0 components) *if you bundle them*. Normal `pip install` does not bundle
them, so for a source/PyPI release there is nothing further to do beyond shipping your own
LICENSE.

## Runtime dependency (always installed)

| Package | Version | License | Notes |
|---------|---------|---------|-------|
| numpy | 2.4.6 | BSD-3-Clause (bundles 0BSD, MIT, Zlib for vendored parts) | The only hard runtime dependency. All permissive. |

## Optional extras (installed only when the corresponding extra is selected)

| Package | Version | License | Extra | Notes |
|---------|---------|---------|-------|-------|
| anthropic | 0.104.1 | MIT | `claude` | Claude text-ops backend. |
| fastapi | 0.136.3 | MIT | `server` | HTTP boundary. |
| uvicorn | 0.48.0 | BSD-3-Clause | `server` | ASGI server. |
| websockets | 16.0 | BSD-3-Clause | `server` | WS event bridge. |
| cryptography | 48.0.0 | Apache-2.0 OR BSD-3-Clause | `vault` | Encrypted vault (Fernet). Dual-licensed — you may take either. |
| sentence-transformers | 5.5.1 | Apache-2.0 | `local` | Local MiniLM embeddings. Pulls torch + transformers + huggingface-hub. |
| torch | 2.12.0 | BSD-3-Clause | `local` (transitive) | Heavy; only via the `local` extra. |
| transformers | 5.9.0 | Apache-2.0 | `local` (transitive) | |
| huggingface-hub | 1.16.1 | Apache-2.0 | `local` (transitive) | |
| faiss-cpu | 1.14.2 | MIT | `ann` | Wheel-friendly ANN backend. |

## Dev-only (not shipped to end users)

| Package | Version | License |
|---------|---------|---------|
| pytest | 9.0.3 | MIT |
| ruff | 0.15.14 | MIT |
| mypy | 2.1.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |

## Caveats / before a release

- **Re-audit at release time.** This snapshot reflects today's installed versions. Run a
  formal pass with a tool such as `pip-licenses` (`pip install pip-licenses && pip-licenses
  --format=markdown --with-urls`) against the exact pinned set you ship.
- **Apache-2.0 components** (cryptography, sentence-transformers, transformers,
  huggingface-hub): if you ever distribute a *bundle* that includes them, include their
  `NOTICE` files. A pip/source install pulls them from PyPI, so this only applies to
  vendored/packaged distributions.
- **Model weights are not bundled.** The `local` extra downloads embedding models (e.g.
  `all-MiniLM-L6-v2`, Apache-2.0) at runtime from Hugging Face — those carry their own
  licenses and are not part of this repo.
- **Eval datasets are not committed.** The LongMemEval dataset used by `evals/` is fetched
  from Hugging Face on demand and is not in the repo; check its terms before distributing
  any eval adapters or results derived from it.
- The core engine depends only on numpy — a minimal, fully-permissive footprint. The heavy
  optional stack (torch/transformers) is opt-in.
