"""Local, offline-capable real embeddings via sentence-transformers (optional).

Selected with ``HydroConfig.embedding_backend == "local"`` (env
``HYDRO_EMBEDDING_BACKEND=local``). Requires the optional ``local`` extra
(``sentence-transformers`` + torch). The model is loaded lazily on first
``embed`` — the default is all-MiniLM-L6-v2 (384-dim, unit-normalized) — so
importing this module never requires the dependency and the offline stub path is
unaffected.

Unlike the hashing :class:`~hydromemory.intelligence.stub.StubEmbedder` (which
only matches shared tokens), this captures real semantic relatedness — e.g.
"I was dismissed during a meeting" vs "being ignored in public" score as similar.
The store's ``vector_dim`` must match the model dimension (384 for the default);
``HydroConfig.from_env`` defaults ``vector_dim`` to 384 when this backend is set.
"""
from __future__ import annotations

from typing import Any

from hydromemory.intelligence.base import Embedder

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_KNOWN_DIMS: dict[str, int] = {"sentence-transformers/all-MiniLM-L6-v2": 384}


class LocalEmbedder(Embedder):
    """Real embeddings from a local sentence-transformers model (lazy-loaded)."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self.dim = _KNOWN_DIMS.get(model_name, 384)
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy, optional dep

            self._model = SentenceTransformer(self.model_name)
            # Method was renamed across versions; prefer the newer name.
            get_dim = getattr(self._model, "get_embedding_dimension", None) or getattr(
                self._model, "get_sentence_embedding_dimension", None
            )
            if get_dim is not None:
                self.dim = int(get_dim())
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._ensure_model()
        vector = model.encode([text], normalize_embeddings=True)[0]
        return [float(x) for x in vector]
