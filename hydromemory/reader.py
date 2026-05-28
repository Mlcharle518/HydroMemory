"""Reader — compose an answer over a recalled constellation, with citations (ADR-0035).

This turns recall into end-to-end QA: given a query and the droplets recall surfaced
(ideally the spreading-activation constellation from ``traverse=True``), compose an
answer and record which droplets it drew from (citations). It realizes the "LLM
reader over the activated subgraph" the research note called for
(``docs/research/memory-as-interacting-network.md`` §5).

The *composition* step is pluggable, in the same stub-first spirit as the rest of
the protocol:

* the **offline default** is a deterministic *extractive* composer (surface the
  top-ranked droplet, cite it) — no network, used by the stub backend and tests;
* an **LLM composer** (Claude) does real abstractive composition and is selected
  when ``intelligence_backend='claude'`` is configured.

A composer may cite context items as ``[n]`` (1-based); :func:`compose_answer` maps
those back to droplet ids so callers get verifiable provenance.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hydromemory.config import HydroConfig
    from hydromemory.schema import Droplet

# (query, numbered context items) -> raw answer text (optionally citing items as [n]).
Composer = Callable[[str, list[str]], str]

_CITATION = re.compile(r"\[(\d+)\]")
_NO_ANSWER = "I don't have enough information to answer that from memory."


@dataclass
class ReaderResult:
    """A composed answer plus its provenance."""

    answer: str
    citations: list[str] = field(default_factory=list)  # droplet ids the answer cited
    context_ids: list[str] = field(default_factory=list)  # all droplets given to the composer


def _extractive_composer(query: str, items: list[str]) -> str:
    """Deterministic offline default: surface the top-ranked item, cited as ``[1]``."""
    if not items:
        return _NO_ANSWER
    return f"{items[0]} [1]"


def build_composer(config: HydroConfig) -> Composer | None:
    """An LLM composer when the Claude backend is configured, else ``None`` (extractive).

    Lazy: ``anthropic`` is imported only when the composer actually runs, so the
    offline/default path never requires it.
    """
    if getattr(config, "intelligence_backend", "stub") != "claude":
        return None

    def _claude_composer(query: str, items: list[str]) -> str:
        from hydromemory.intelligence.claude_backend import _ClaudeClient

        block = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
        system = (
            "Answer the question using ONLY the numbered context items. Be concise. "
            "Cite the item numbers you used in square brackets, e.g. [1][3]. If the "
            "context does not contain the answer, say you don't have enough information."
        )
        user = f"Context:\n{block}\n\nQuestion: {query}\nAnswer:"
        return _ClaudeClient(config).complete_text(system, user, max_tokens=160)

    return _claude_composer


def compose_answer(
    query: str,
    droplets: Sequence[Droplet],
    *,
    composer: Composer | None = None,
    max_context: int = 12,
) -> ReaderResult:
    """Compose an answer to ``query`` from ``droplets`` (highest-ranked first).

    Uses ``composer`` (default: the offline extractive composer). Citations are the
    droplet ids for any ``[n]`` markers the composer emitted (1-based, clamped to the
    provided context). ``max_context`` caps how many droplets are handed to the composer.
    """
    used = list(droplets)[:max_context]
    if not used:
        return ReaderResult(answer=_NO_ANSWER, citations=[], context_ids=[])
    items = [d.content for d in used]
    raw = (composer or _extractive_composer)(query, items)
    cited = sorted({int(n) for n in _CITATION.findall(raw)})
    citations = [used[i - 1].id for i in cited if 1 <= i <= len(used)]
    return ReaderResult(answer=raw.strip(), citations=citations, context_ids=[d.id for d in used])


__all__ = ["ReaderResult", "Composer", "compose_answer", "build_composer"]
