"""Intelligence factory + interface re-exports.

``build_intelligence`` selects the backend from config/env
(``HYDRO_INTELLIGENCE_BACKEND``), defaulting to the offline ``stub`` backend.
The Claude backend is imported lazily so a missing ``anthropic`` package or API
key never breaks the offline path.
"""
from __future__ import annotations

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

__all__ = [
    "build_intelligence",
    "Intelligence",
    "Embedder",
    "Abstractor",
    "Classifier",
    "ContaminationDetector",
    "Classification",
    "ContaminationVerdict",
]


def _build_embedder(config: HydroConfig) -> Embedder:
    """Select the embedder by ``embedding_backend`` (independent of text-ops)."""
    backend = (config.embedding_backend or "stub").lower()
    if backend == "local":
        from hydromemory.intelligence.local_backend import LocalEmbedder

        return LocalEmbedder()
    # "stub" (default) and "claude" (Anthropic exposes no embeddings API) -> stub.
    from hydromemory.intelligence.stub import StubEmbedder

    return StubEmbedder(config.vector_dim)


def build_intelligence(config: HydroConfig | None = None) -> Intelligence:
    """Compose an Intelligence bundle.

    Text ops (abstractor/classifier/contamination detector) are selected by
    ``intelligence_backend`` (``stub`` | ``claude``); the embedder is selected
    independently by ``embedding_backend`` (``stub`` | ``local``). The all-``stub``
    default reproduces the v1 offline bundle exactly.
    """
    config = config or HydroConfig.from_env()
    text_backend = (config.intelligence_backend or "stub").lower()
    if text_backend == "claude":
        from hydromemory.intelligence.claude_backend import build_claude_intelligence

        base = build_claude_intelligence(config)
    else:
        from hydromemory.intelligence.stub import build_stub_intelligence

        base = build_stub_intelligence(config)
    return Intelligence(
        embedder=_build_embedder(config),
        abstractor=base.abstractor,
        classifier=base.classifier,
        detector=base.detector,
    )
