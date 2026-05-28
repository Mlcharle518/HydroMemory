"""Proof that real (local) embeddings beat the hash stub on semantics.

The embedder-dependent tests skip when ``sentence-transformers`` isn't installed
(the offline default); install the ``local`` extra to run them. The
abstraction-bonus lever test needs no model and always runs.
"""
from __future__ import annotations

import pytest

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.intelligence.stub import StubEmbedder
from hydromemory.recall import RecallWeights, hydro_recall_score
from hydromemory.schema import Droplet, Phase, State

ABSTRACTION = "being ignored in public"
LITERAL = "I was dismissed during a meeting"
UNRELATED = "how to bake sourdough bread on the weekend"
RECALL_QUERY = "feeling ignored and dismissed by colleagues"


def _cos(a: list[float], b: list[float]) -> float:
    # Both backends return unit-normalized vectors, so dot product is cosine.
    return sum(x * y for x, y in zip(a, b, strict=False))


def test_local_embedder_captures_semantics_stub_does_not():
    pytest.importorskip("sentence_transformers")
    from hydromemory.intelligence.local_backend import LocalEmbedder

    local = LocalEmbedder()
    anchor = local.embed(LITERAL)
    sim_related = _cos(anchor, local.embed(ABSTRACTION))
    sim_unrelated = _cos(anchor, local.embed(UNRELATED))

    # Real semantics: "dismissed in a meeting" is far closer to "ignored in
    # public" (a true paraphrase that shares NO words) than to an unrelated topic.
    assert sim_related > sim_unrelated + 0.1

    # The hash stub keys on shared tokens; LITERAL and ABSTRACTION share none, so
    # it scores ~0 and entirely misses the relation the local model captures.
    stub = StubEmbedder(256)
    assert sim_related > _cos(stub.embed(LITERAL), stub.embed(ABSTRACTION)) + 0.1


def test_local_recall_surfaces_relevant_over_unrelated(tmp_path):
    pytest.importorskip("sentence_transformers")
    cfg = HydroConfig(
        db_path=str(tmp_path / "real.db"),
        vector_dim=384,
        embedding_backend="local",
        intelligence_backend="stub",
    )
    engine = build_engine(cfg)
    try:
        dismissal = engine.absorb(LITERAL, context={"topic": "work"})
        abstraction = engine.absorb(ABSTRACTION, context={"topic": "social"})
        cooking = engine.absorb(UNRELATED, context={"topic": "cooking"})

        results = engine.recall(
            RECALL_QUERY, agent=AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED)
        )
        ids = [r.droplet_id for r in results]
        assert ids, "expected real-embedding recall to return hits"
        # The semantically-distant cooking memory must not be the top hit.
        assert results[0].droplet_id != cooking["droplet_id"]
        # A relevant memory (the dismissal or its abstraction) is recalled.
        assert dismissal["droplet_id"] in ids or abstraction["droplet_id"] in ids
    finally:
        engine.close()


def test_abstraction_bonus_lever_promotes_patterns():
    """The recall-ranking gap + its fix, with no model needed (deterministic).

    Same passed-in semantic_similarity for a literal LIQUID source and a VAPOR
    abstraction: by default the literal's higher phase-accessibility wins (the
    documented gap); ``abstraction_bonus`` lets the pattern catch up / overtake.
    """
    liquid = Droplet(id="L", phase=Phase.LIQUID, state=State())
    vapor = Droplet(id="V", phase=Phase.VAPOR, state=State())
    terms = dict(
        semantic_similarity=0.5,
        permission_score=1.0,
        privacy_risk=0.0,
        contamination_penalty=0.0,
    )

    base_liquid = hydro_recall_score(liquid, {}, **terms)
    base_vapor = hydro_recall_score(vapor, {}, **terms)
    assert base_liquid > base_vapor  # the gap: literal out-ranks the abstraction

    boosted_vapor = hydro_recall_score(
        vapor, {}, weights=RecallWeights(abstraction_bonus=0.6), **terms
    )
    assert boosted_vapor > base_vapor
    assert boosted_vapor >= base_liquid  # the lever closes/flips the gap
