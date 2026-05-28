"""Pluggable intelligence interfaces (PRD §5.6, §6, §10.1).

Operations that need real NLP are expressed behind small ABCs so a deterministic
offline *stub* backend (default) and an optional *Claude + embeddings* backend can
be swapped without touching the engine:

- :class:`Embedder`  -> semantic_similarity vectors
- :class:`Abstractor` -> EVAPORATE (content -> pattern/essence)
- :class:`Classifier` -> §6 classification block
- :class:`ContaminationDetector` -> §10.1 contamination assessment
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hydromemory.schema import Droplet


@dataclass
class Classification:
    """Output of :meth:`Classifier.classify` (mirrors the §6 classification block)."""

    memory_type: str
    importance: float
    sensitivity: float
    expected_lifespan: str  # temporary | persistent | archived


@dataclass
class ContaminationVerdict:
    """Output of :meth:`ContaminationDetector.assess` (PRD §10.1)."""

    contaminated: bool
    reason: str
    confidence: float


# Controlled vocabulary for ``memory_type`` so HQL ``type="..."`` filters are
# predictable across backends. The stub classifier's labels are a subset; the
# Claude backend is constrained to this set (structured-output enum) and coerced
# as a backstop, so free-form model labels can't leak into the store.
MEMORY_TYPES: tuple[str, ...] = (
    "communication_preference",
    "cognitive_style",
    "preference",
    "value",
    "identity",
    "factual",
    "procedural",
    "emotional",
    "relationship",
    "task",
    "general",
)


def coerce_memory_type(value: object) -> str:
    """Map a raw ``memory_type`` into :data:`MEMORY_TYPES`; unknown -> ``'general'``."""
    text = str(value or "").strip().lower()
    return text if text in MEMORY_TYPES else "general"


class Embedder(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a fixed-dimension embedding vector for ``text``."""


class Abstractor(ABC):
    @abstractmethod
    def evaporate(self, content: str) -> str:
        """Abstract ``content`` into a pattern/essence (the EVAPORATE verb)."""


class Classifier(ABC):
    @abstractmethod
    def classify(self, content: str) -> Classification:
        """Classify memory_type, importance, sensitivity, and expected lifespan."""


class ContaminationDetector(ABC):
    @abstractmethod
    def assess(self, droplet: Droplet, context: dict[str, Any]) -> ContaminationVerdict:
        """Assess whether a droplet is contaminated (unreliable/contradictory/etc.)."""


@dataclass
class Intelligence:
    """Bundle of the four backends, produced by ``build_intelligence``."""

    embedder: Embedder
    abstractor: Abstractor
    classifier: Classifier
    detector: ContaminationDetector
