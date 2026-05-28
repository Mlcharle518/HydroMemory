"""LocalEmbedder dimension-detection tests (fully offline).

``LocalEmbedder._ensure_model`` lazily does ``from sentence_transformers import
SentenceTransformer``, constructs the model, then reads its embedding dimension
via whichever method the installed version exposes — the newer
``get_embedding_dimension`` or the older ``get_sentence_embedding_dimension`` —
and sets ``self.dim`` from it.

These tests inject a *fake* ``sentence_transformers`` module into ``sys.modules``
so no real model (and no torch download) is ever loaded; they therefore run
offline and deterministically regardless of whether the optional ``local`` extra
is installed.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from hydromemory.intelligence.local_backend import DEFAULT_MODEL, LocalEmbedder


class _FakeModelNewDim:
    """A sentence-transformers model exposing ONLY the newer dim method."""

    def __init__(self, dim: int) -> None:
        self._dim = dim

    def get_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]


class _FakeModelOldDim:
    """A sentence-transformers model exposing ONLY the older dim method."""

    def __init__(self, dim: int) -> None:
        self._dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]


def _install_fake_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch, model_factory: Any
) -> None:
    """Inject a fake ``sentence_transformers`` module whose ``SentenceTransformer``
    constructor ignores its args and returns ``model_factory()``.
    """
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = lambda *args, **kwargs: model_factory()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


def test_local_embedder_default_dim_before_model_load() -> None:
    # Construction must not touch the optional dependency; dim is the known default.
    embedder = LocalEmbedder()
    assert embedder.model_name == DEFAULT_MODEL
    assert embedder.dim == 384
    assert embedder._model is None


def test_dim_detected_via_new_method_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch, lambda: _FakeModelNewDim(768))
    embedder = LocalEmbedder(model_name="custom/model-768")
    # Pre-load the dim falls back to the 384 default for an unknown model name.
    assert embedder.dim == 384
    embedder._ensure_model()
    assert embedder.dim == 768  # detected from get_embedding_dimension()


def test_dim_detected_via_old_method_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch, lambda: _FakeModelOldDim(512))
    embedder = LocalEmbedder(model_name="custom/model-512")
    embedder._ensure_model()
    assert embedder.dim == 512  # detected from get_sentence_embedding_dimension()


def test_embed_uses_loaded_model_and_normalizes_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch, lambda: _FakeModelNewDim(16))
    embedder = LocalEmbedder(model_name="custom/model-16")
    vector = embedder.embed("hello world")
    assert isinstance(vector, list)
    assert len(vector) == 16
    assert all(isinstance(x, float) for x in vector)
    # The model was cached after first use (lazy-load happened exactly once).
    assert embedder._model is not None
