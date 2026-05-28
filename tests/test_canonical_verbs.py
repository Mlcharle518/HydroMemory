"""Canonical §18 protocol verb registry + resolver — open-core build (ADR-0048).

In the open core only the three memory verbs (ABSORB / RECALL / FORGET) are implemented; the
nine upper-layer verbs (SENSE, ANCHOR, FORM_INTENT, JUDGE, PLAN, ACT, REFLECT, INTEGRATE,
SUPERSEDE) are part of the commercial HydroCognitive Stack and stay ``implemented=False`` here.
"""
from __future__ import annotations

import pytest

from hydromemory.canonical.verbs import VERB_REGISTRY, CanonicalVerb, resolve_verb
from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine


_MEMORY_VERBS = {CanonicalVerb.ABSORB, CanonicalVerb.RECALL, CanonicalVerb.FORGET}
_UPPER_VERBS = set(CanonicalVerb) - _MEMORY_VERBS


@pytest.fixture
def engine(tmp_path):
    eng = build_engine(HydroConfig(db_path=str(tmp_path / "h.db"), vector_dim=64))
    yield eng
    eng.close()


def test_registry_has_all_twelve_verbs():
    assert set(VERB_REGISTRY) == set(CanonicalVerb)


def test_only_memory_verbs_are_implemented_in_open_core():
    implemented = {v for v, spec in VERB_REGISTRY.items() if spec.implemented}
    assert implemented == _MEMORY_VERBS


def test_memory_verbs_resolve_on_the_engine(engine):
    for verb in _MEMORY_VERBS:
        bound = resolve_verb(verb, engine)
        assert bound, f"{verb} should resolve to at least one bound method"
        assert all(callable(m) for m in bound)


def test_upper_layer_verbs_do_not_resolve(engine):
    for verb in _UPPER_VERBS:
        assert resolve_verb(verb, engine) == []


def test_resolve_accepts_string_alias(engine):
    assert resolve_verb("ABSORB", engine)
