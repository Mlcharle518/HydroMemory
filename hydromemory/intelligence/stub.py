"""Deterministic, offline stub intelligence backend (the default).

Every operation here is pure and reproducible with no network and no API key, so
the engine's default path works in CI and on a laptop with nothing configured:

- :class:`StubEmbedder` — a stable hashing-trick bag-of-words embedding. Identical
  text yields an identical vector across processes (it uses :mod:`hashlib`, never
  the salted builtin ``hash``); texts sharing words score higher cosine.
- :class:`StubAbstractor` — heuristic EVAPORATE: strips first-person specifics and
  concrete detail, keeping a short gist phrase.
- :class:`StubClassifier` — keyword heuristics for memory_type / importance /
  sensitivity / expected_lifespan.
- :class:`StubContaminationDetector` — the PRD §10.1 contamination rules.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Any

from hydromemory.config import HydroConfig
from hydromemory.intelligence.base import (
    Abstractor,
    Classification,
    Classifier,
    ContaminationDetector,
    ContaminationVerdict,
    Embedder,
    Intelligence,
)

if TYPE_CHECKING:
    from hydromemory.schema import Droplet

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


# ----------------------------------------------------------------- embedder
class StubEmbedder(Embedder):
    """Stable hashing-trick embedder producing unit-length vectors of ``dim``."""

    def __init__(self, dim: int) -> None:
        self.dim = int(dim)

    def _bucket_and_sign(self, token: str) -> tuple[int, float]:
        """Map a token to a (bucket, sign) using a stable SHA-256 digest."""
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") % self.dim
        sign = 1.0 if (digest[8] & 1) == 0 else -1.0
        return bucket, sign

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _tokens(text):
            bucket, sign = self._bucket_and_sign(token)
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


# ---------------------------------------------------------------- abstractor
# First-person / concrete markers dropped during EVAPORATE.
_FIRST_PERSON = {"i", "me", "my", "mine", "we", "us", "our", "ours"}
_FILLER = {"a", "an", "the", "was", "were", "is", "are", "am", "been", "being",
           "during", "at", "in", "on", "of", "to", "for", "with", "by", "and",
           "or", "but", "that", "this", "it", "he", "she", "they", "them"}
# A few concrete->abstract gist rewrites for the canonical PRD §12 example.
_GIST_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdismiss(ed|ing)?\b"), "being ignored"),
    (re.compile(r"\bignor(ed|ing)\b"), "being ignored"),
    (re.compile(r"\bmeeting\b"), "in public"),
    (re.compile(r"\binterrupt(ed|s|ing)?\b"), "being ignored"),
)


class StubAbstractor(Abstractor):
    """Heuristic EVAPORATE: distill content to a short, person-agnostic gist."""

    def evaporate(self, content: str) -> str:
        text = content.strip().lower().rstrip(".!?")
        for pattern, repl in _GIST_REWRITES:
            text = pattern.sub(repl, text)
        kept = [
            tok
            for tok in _tokens(text)
            if tok not in _FIRST_PERSON and tok not in _FILLER
        ]
        if not kept:
            # Fall back to the (de-personalized) original gist.
            kept = [tok for tok in _tokens(text) if tok not in _FIRST_PERSON]
        # De-duplicate while preserving order (gist, not a sentence).
        seen: set[str] = set()
        gist: list[str] = []
        for tok in kept:
            if tok not in seen:
                seen.add(tok)
                gist.append(tok)
        return " ".join(gist)


# ---------------------------------------------------------------- classifier
# Keyword -> memory_type heuristics (ordered; first match wins).
_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("communication_preference", ("prefer", "style", "tone", "concise", "verbose", "depth")),
    ("value", ("value", "believe", "principle", "vow", "never", "always")),
    ("emotional", ("felt", "feel", "angry", "sad", "hurt", "afraid", "anxious", "happy")),
    ("factual", ("is", "was", "fact", "born", "located", "equals", "number")),
    ("procedural", ("how", "step", "process", "first", "then", "workflow")),
)
_SENSITIVE_WORDS = {
    "password", "ssn", "secret", "private", "medical", "health", "diagnosis",
    "salary", "address", "phone", "afraid", "ashamed", "trauma",
}


class StubClassifier(Classifier):
    """Keyword-heuristic classifier for the §6 classification block."""

    def classify(self, content: str) -> Classification:
        toks = _tokens(content)
        tokset = set(toks)

        memory_type = "general"
        for label, keywords in _TYPE_RULES:
            if tokset.intersection(keywords):
                memory_type = label
                break

        # Importance: longer, preference/value-laden content scores higher.
        importance = 0.4
        if memory_type in {"communication_preference", "value"}:
            importance += 0.3
        importance += min(0.2, len(toks) / 100.0)
        importance = _clamp01(importance)

        # Sensitivity: presence of sensitive markers.
        hits = len(tokset.intersection(_SENSITIVE_WORDS))
        sensitivity = _clamp01(0.1 + 0.3 * hits)

        # Lifespan: values/preferences persist; sensitive -> archived; else temporary.
        if memory_type in {"value", "communication_preference"}:
            lifespan = "persistent"
        elif hits > 0:
            lifespan = "archived"
        else:
            lifespan = "temporary"

        return Classification(
            memory_type=memory_type,
            importance=importance,
            sensitivity=sensitivity,
            expected_lifespan=lifespan,
        )


# --------------------------------------------------------- contamination
# PRD §10.1 markers.
_CONTRADICTION_MARKERS = ("actually", "not true", "that's wrong", "thats wrong",
                          "i was wrong", "correction", "scratch that", "never mind")
_MANIPULATION_MARKERS = ("ignore previous", "ignore all", "disregard", "pretend",
                         "jailbreak", "you must", "override", "system prompt")


class StubContaminationDetector(ContaminationDetector):
    """Rule-based contamination assessment (PRD §10.1).

    Triggers (any one ⇒ contaminated):

    - source unreliable (``context['source_reliable'] is False`` or low
      ``context['source_trust']``);
    - explicit contradiction / "user later corrects it" markers in the content or
      ``context['correction']``;
    - manipulation markers in the content (``context['manipulated']`` forces it);
    - "agent inferred too much": low confidence (``state.confidence`` or
      ``context['confidence']`` below 0.35);
    - emotionally intense but factually uncertain: high ``emotional_charge``
      (>= 0.6) together with low confidence (< 0.5).
    """

    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        context = context or {}
        content = (droplet.content or "").lower()
        confidence = _coerce_float(
            context.get("confidence"), default=droplet.state.confidence
        )
        charge = _coerce_float(
            context.get("emotional_charge"), default=droplet.state.emotional_charge
        )

        # Source unreliable.
        source_reliable = context.get("source_reliable")
        source_trust = context.get("source_trust")
        if source_reliable is False or (
            source_trust is not None and _coerce_float(source_trust, 1.0) < 0.3
        ):
            return ContaminationVerdict(True, "Source is unreliable.", 0.8)

        # Manipulation.
        if context.get("manipulated") is True or any(
            m in content for m in _MANIPULATION_MARKERS
        ):
            return ContaminationVerdict(True, "Input may be manipulated.", 0.85)

        # Contradiction / user later corrects it.
        if context.get("correction") or any(m in content for m in _CONTRADICTION_MARKERS):
            return ContaminationVerdict(
                True, "Memory contradicts verified facts or was later corrected.", 0.8
            )

        # Agent inferred too much (low confidence inference).
        if confidence < 0.35:
            return ContaminationVerdict(
                True, "Low-confidence inference; agent inferred too much.", 0.7
            )

        # Emotionally intense but factually uncertain.
        if charge >= 0.6 and confidence < 0.5:
            return ContaminationVerdict(
                True,
                "Low confidence inference from emotionally charged conversation.",
                0.75,
            )

        return ContaminationVerdict(False, "No contamination markers detected.", 0.6)


# ------------------------------------------------------------------ helpers
def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def build_stub_intelligence(config: HydroConfig) -> Intelligence:
    """Bundle the four deterministic stub backends into an :class:`Intelligence`."""
    return Intelligence(
        embedder=StubEmbedder(config.vector_dim),
        abstractor=StubAbstractor(),
        classifier=StubClassifier(),
        detector=StubContaminationDetector(),
    )
