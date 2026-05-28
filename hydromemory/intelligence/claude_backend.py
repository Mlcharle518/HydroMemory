"""Optional Claude-backed intelligence (Abstractor / Classifier / ContaminationDetector).

Selected via ``HydroConfig.intelligence_backend == "claude"``. ``anthropic`` (and
``pydantic``, which it ships with) are imported lazily inside the client, so
importing this module never requires the package and the offline stub path is
unaffected.

Hardening (see docs/adr): structured output via ``client.messages.parse()`` with
Pydantic schemas (no brittle JSON scraping), with a defensive ``messages.create``
+ JSON fallback for older SDKs; the model defaults to ``claude-opus-4-7`` and is
configurable (``HYDRO_CLAUDE_MODEL``); a stable system prompt per op carries a
``cache_control`` breakpoint; typed-exception error handling; no extended thinking
(these are short classification/abstraction calls). Embeddings: Anthropic exposes
no embeddings endpoint, so the embedder is chosen separately
(``HYDRO_EMBEDDING_BACKEND`` = ``stub`` | ``local``); this backend defaults to the
deterministic :class:`StubEmbedder`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from hydromemory.config import HydroConfig
from hydromemory.intelligence.base import (
    MEMORY_TYPES,
    Abstractor,
    Classification,
    Classifier,
    ContaminationDetector,
    ContaminationVerdict,
    Intelligence,
    coerce_memory_type,
)
from hydromemory.intelligence.stub import StubEmbedder

if TYPE_CHECKING:
    from hydromemory.schema import Droplet

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_MODEL = "claude-opus-4-7"
# Per-request timeout (seconds) for the Anthropic client so a hung API call can't
# block a capture/recall pass indefinitely (these are short classification calls).
DEFAULT_CLAUDE_TIMEOUT = 30.0
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Stable per-op system prompts. NOTE: prompt caching is a prefix match with a
# ~4096-token minimum on Opus 4.7 — these prompts are far shorter, so the
# cache_control breakpoint below is a no-op today and only starts paying off if
# the prompts grow (e.g. with few-shot examples). Kept for that forward path.
_ABSTRACT_SYSTEM = (
    "You distill a memory into its essence: a short, general pattern phrase with "
    "no first-person pronouns and no concrete specifics. Reply with ONLY the phrase."
)
_CLASSIFY_SYSTEM = (
    "You classify a memory for a hydraulic memory system. Return: memory_type "
    "(exactly one of: " + ", ".join(MEMORY_TYPES) + "), importance and sensitivity "
    "(each 0..1), and expected_lifespan (temporary, persistent, or archived)."
)
_CONTAM_SYSTEM = (
    "You assess whether a memory is contaminated — unreliable source, contradicts "
    "known facts, was later corrected, over-inferred, possibly manipulated input, or "
    "emotionally intense yet factually uncertain. Return contaminated (bool), a short "
    "reason, and confidence (0..1)."
)


def _clamp01(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


# --- Lazy Pydantic schemas for structured output (built once on first use) ---
_SCHEMAS: dict[str, Any] = {}


def _classification_schema() -> Any:
    if "classification" not in _SCHEMAS:
        from typing import Literal

        from pydantic import BaseModel

        class _Classification(BaseModel):
            # Constrain to the controlled vocabulary so structured decoding can't
            # emit a free-form label; ``Literal[tuple]`` expands at runtime.
            memory_type: Literal[MEMORY_TYPES]  # type: ignore[valid-type]
            importance: float
            sensitivity: float
            expected_lifespan: Literal["temporary", "persistent", "archived"]

        _SCHEMAS["classification"] = _Classification
    return _SCHEMAS["classification"]


def _verdict_schema() -> Any:
    if "verdict" not in _SCHEMAS:
        from pydantic import BaseModel

        class _Verdict(BaseModel):
            contaminated: bool
            reason: str
            confidence: float

        _SCHEMAS["verdict"] = _Verdict
    return _SCHEMAS["verdict"]


def _parse_json_object(text: str) -> dict[str, Any]:
    """Defensive fallback: pull the first JSON object out of a text response."""
    text = (text or "").strip()
    candidates = [text]
    match = _JSON_RE.search(text)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return {}


class _ClaudeClient:
    """Lazy Anthropic Messages client with structured-output + text helpers."""

    def __init__(self, config: HydroConfig) -> None:
        self.api_key = config.anthropic_api_key
        self.model = getattr(config, "claude_model", None) or DEFAULT_CLAUDE_MODEL
        self._client: Any = None

    def _ensure(self) -> Any:
        if not self.api_key:
            raise RuntimeError(
                "Claude intelligence backend requires an Anthropic API key "
                "(set ANTHROPIC_API_KEY or HydroConfig.anthropic_api_key)."
            )
        if self._client is None:
            import anthropic  # lazy: never required at import time

            # ``timeout`` bounds each request (connect + read) so a hung call
            # surfaces as a typed timeout error instead of blocking forever.
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=DEFAULT_CLAUDE_TIMEOUT)
        return self._client

    def _system(self, text: str) -> list[dict[str, Any]]:
        # cache_control is harmless below the cache minimum (see module note).
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    def complete_text(self, system: str, user: str, *, max_tokens: int = 64) -> str:
        client = self._ensure()
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=self._system(system),
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001 -- annotate + re-raise for diagnosis
            raise RuntimeError(f"Claude completion failed ({type(exc).__name__}): {exc}") from exc
        return "".join(
            getattr(b, "text", "") for b in (resp.content or []) if getattr(b, "type", None) == "text"
        ).strip()

    def parse_object(self, system: str, user: str, schema: Any, *, max_tokens: int = 256) -> dict[str, Any]:
        """Return a dict from a structured-output call, falling back defensively.

        Prefers ``messages.parse(output_format=schema)`` (validated Pydantic). If
        the installed SDK lacks ``parse``, falls back to ``messages.create`` with a
        JSON instruction + defensive extraction.
        """
        client = self._ensure()
        parse = getattr(client.messages, "parse", None)
        if callable(parse):
            try:
                resp = parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=self._system(system),
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                )
                parsed = getattr(resp, "parsed_output", None)
                if parsed is not None:
                    return parsed.model_dump()
            except Exception as exc:  # noqa: BLE001 -- fall through to the text path
                logger.warning("Claude structured parse failed (%s); falling back to text+JSON.", exc)
        # Fallback: ask for JSON in the system prompt and extract defensively.
        text = self.complete_text(
            system + " Reply with ONLY a single minified JSON object.",
            user,
            max_tokens=max_tokens,
        )
        return _parse_json_object(text)


class ClaudeAbstractor(Abstractor):
    def __init__(self, client: _ClaudeClient) -> None:
        self._client = client

    def evaporate(self, content: str) -> str:
        return self._client.complete_text(_ABSTRACT_SYSTEM, f"Memory: {content}", max_tokens=64).strip('"')


class ClaudeClassifier(Classifier):
    def __init__(self, client: _ClaudeClient) -> None:
        self._client = client

    def classify(self, content: str) -> Classification:
        data = self._client.parse_object(
            _CLASSIFY_SYSTEM, f"Memory: {content}", _classification_schema(), max_tokens=256
        )
        return Classification(
            memory_type=coerce_memory_type(data.get("memory_type", "general")),
            importance=_clamp01(data.get("importance", 0.5)),
            sensitivity=_clamp01(data.get("sensitivity", 0.1)),
            expected_lifespan=str(data.get("expected_lifespan", "temporary")),
        )


class ClaudeContaminationDetector(ContaminationDetector):
    def __init__(self, client: _ClaudeClient) -> None:
        self._client = client

    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        user = f"Memory: {droplet.content}\nContext: {json.dumps(context or {}, sort_keys=True)}"
        data = self._client.parse_object(_CONTAM_SYSTEM, user, _verdict_schema(), max_tokens=256)
        return ContaminationVerdict(
            contaminated=bool(data.get("contaminated", False)),
            reason=str(data.get("reason", "")),
            confidence=_clamp01(data.get("confidence", 0.5)),
        )


def build_claude_intelligence(config: HydroConfig) -> Intelligence:
    """Build a Claude-backed bundle (embeddings reuse :class:`StubEmbedder`).

    The Anthropic client is constructed lazily on first call; a missing API key
    raises a clear ``RuntimeError`` only when a Claude-backed method is invoked.
    """
    client = _ClaudeClient(config)
    return Intelligence(
        embedder=StubEmbedder(config.vector_dim),
        abstractor=ClaudeAbstractor(client),
        classifier=ClaudeClassifier(client),
        detector=ClaudeContaminationDetector(client),
    )
