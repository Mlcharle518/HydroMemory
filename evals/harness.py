"""Shared eval harness: result record, engine builder, corpus ingestion."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

from evals.datasets import CorpusDroplet
from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.schema import State


@dataclass(frozen=True)
class EvalResult:
    eval: str  # e.g. "multihop"
    condition: str  # e.g. "baseline_cosine" | "traverse"
    metric: str  # e.g. "support_recall"
    value: float
    n: int  # number of cases aggregated
    detail: dict[str, Any] = field(default_factory=dict)


# Default hydraulic state for ingested corpus droplets. fluidity > 0 so spreading
# activation can CONDUCT out of a node, and purity high so contamination_penalty
# (1 - purity) is small. NOTE: bare ``Verbs.absorb`` seeds an all-zero State, which
# makes traversal inert (conductance 0) — the eval seeds a conductive state so the
# mechanism is exercised. (A real finding: production capture must give absorbed
# droplets non-zero fluidity, or links never carry activation.)
_EVAL_STATE = dict(fluidity=0.8, purity=0.9, pressure=0.4, gravity=0.4, confidence=0.8)


def build_eval_engine(
    *,
    backend: str = "local",
    vector_backend: str = "brute",
    intents: bool = False,
    integrate: bool = False,
) -> Any:
    """A real Engine for eval recall. ``backend='local'`` = MiniLM embeddings
    (realistic semantics); ``'stub'`` = the offline hash embedder (lexical only).
    ``intents=True`` enables the HydroIntent layer (``engine.intents``);
    ``integrate=True`` enables HydroIntegrate (``engine.integrate``)."""
    dim = 384 if backend == "local" else 256
    db_path = os.path.join(tempfile.mkdtemp(prefix="hydroeval-"), "eval.db")
    cfg = HydroConfig(
        db_path=db_path,
        vector_dim=dim,
        embedding_backend=backend,
        intelligence_backend="stub",  # retrieval evals need only the embedder
        vector_backend=vector_backend,
        intents_enabled=intents,
        integrate_enabled=integrate,
    )
    return build_engine(cfg)


def ingest_corpus(engine: Any, corpus: list[CorpusDroplet]) -> dict[str, str]:
    """Absorb each corpus droplet (real embeddings) and wire its declared links.

    Returns a ``dataset_id -> real_droplet_id`` map (absorb mints fresh ids), used
    to translate the dataset's link targets and the questions' gold-support ids.
    """
    id_map: dict[str, str] = {}
    handles: dict[str, Any] = {}
    for item in corpus:
        state = State(**{**_EVAL_STATE, **item.state})
        droplet = engine.verbs.absorb(item.content, state=state)
        id_map[item.id] = droplet.id
        handles[item.id] = droplet
    for item in corpus:
        for kind, targets in item.links.items():
            real_targets = [id_map[t] for t in targets if t in id_map]
            if real_targets:
                engine.verbs.flow(handles[item.id], real_targets, kind=kind)
    return id_map
