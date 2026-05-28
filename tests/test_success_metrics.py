"""PRD §16 success metrics, encoded as automated assertions over the real stack.

Each test maps to one §16 metric and exercises the fully-wired engine (stub
intelligence, real governance/verbs/storage):

1. Recall precision  -- contextually appropriate, not merely similar.
2. Over-memory reduction -- temporary facts don't become identity-level memory.
3. User trust -- inspect / freeze / drain / forget round-trip and change state.
4. Safety -- sensitive (frozen) and polluted memory is restricted.
5. Interoperability -- common schema, verbs, and protocol round-trip stably.
6. Adaptation -- memory updates without erasing history.
"""
from __future__ import annotations

import pytest

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.governance import AccessContext, AgentIdentity, TrustLevel
from hydromemory.protocol import ProtocolEnvelope
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import STORABLE_PHASES, Droplet, Phase

ALL_VERBS = [
    "absorb", "flow", "evaporate", "condense", "precipitate", "infiltrate",
    "freeze", "melt", "filter", "pollute", "distill", "irrigate", "drain",
    "archive", "forget",
]


@pytest.fixture
def engine(tmp_db_path):
    eng = build_engine(HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub"))
    try:
        yield eng
    finally:
        eng.close()


def test_metric_recall_precision(engine):
    """Recall surfaces the contextually-relevant memory over an unrelated one."""
    arch = engine.absorb(
        "User loves deep software architecture and executable frameworks.",
        context={"topic": "engineering"},
    )
    cook = engine.absorb("User enjoys baking sourdough bread on weekends.", context={"topic": "cooking"})

    results = engine.recall(
        "software architecture frameworks",
        agent=AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED),
    )
    scores = {r.droplet_id: r.score for r in results}
    assert arch["droplet_id"] in scores, "expected the architecture memory to be recalled"
    # The architecture memory ranks at least as high as the cooking one
    # (which may be filtered out entirely by the threshold — even better).
    assert scores.get(arch["droplet_id"], 0.0) >= scores.get(cook["droplet_id"], -1.0)


def test_metric_over_memory_reduction(engine):
    """A one-off transient fact stays shallow and never becomes identity memory."""
    d = engine.absorb("User asked about buying running shoes.", context={"topic": "shopping"})
    droplet = engine.repo.get(d["droplet_id"])
    assert droplet is not None
    assert droplet.phase is Phase.LIQUID
    assert droplet.reservoir in {Reservoir.WORKING_STREAM, Reservoir.SURFACE}
    assert droplet.reservoir not in {Reservoir.GROUNDWATER, Reservoir.SACRED}
    assert droplet.state.depth < 0.3
    assert droplet.cycle.cycle_count == 0


def test_metric_user_trust_controls(engine):
    """The user can freeze, drain, and forget memory — each changes state."""
    user = AgentIdentity(name="user", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True)
    consent = AccessContext(consent_granted=True, thaw_granted=True, safe_context=True)

    # FREEZE -> high-integrity snapshot in the glacier.
    f = engine.absorb("A painful personal memory the user shared.", context={"topic": "personal"})
    frozen = engine.verbs.freeze(engine.repo.get(f["droplet_id"]), agent=user, context=consent)
    assert frozen.phase is Phase.ICE
    assert frozen.reservoir is Reservoir.GLACIER

    # DRAIN -> reduces active influence (pressure + fluidity).
    g = engine.absorb("A passing thought about the weather.", context={"topic": "smalltalk"})
    gd = engine.repo.get(g["droplet_id"])
    gd.state.pressure = 0.8
    gd.state.fluidity = 0.8
    engine.repo.upsert(gd)
    drained = engine.verbs.drain(gd)
    assert (drained.state.pressure + drained.state.fluidity) < 1.6

    # FORGET -> deletion by explicit user command.
    h = engine.absorb("Something to be forgotten.", context={"topic": "temp"})
    response = engine.verbs.forget(engine.repo.get(h["droplet_id"]), agent=user, context=consent)
    assert response.outcome is not None and response.outcome["deleted"] is True
    assert engine.repo.get(h["droplet_id"]) is None


def test_metric_safety_restricts_sensitive_and_polluted(engine):
    """Frozen (glacier) and polluted memory are kept out of ordinary use."""
    user = AgentIdentity(name="user", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True)

    secret = engine.absorb("A sensitive secret about the user's difficult past.", context={"topic": "personal"})
    engine.verbs.freeze(
        engine.repo.get(secret["droplet_id"]),
        agent=user,
        context=AccessContext(consent_granted=True, thaw_granted=True),
    )
    engine.absorb("User likes tidy documentation.", context={"topic": "docs"})

    results = engine.recall(
        "sensitive secret difficult past",
        agent=AgentIdentity(name="assistant", trust_level=TrustLevel.APPROVED),
    )
    assert all(r.droplet_id != secret["droplet_id"] for r in results), (
        "a frozen glacier memory must not surface through ordinary recall"
    )

    polluted_src = engine.absorb("User hates working with teams.", context={"topic": "work"})
    polluted = engine.verbs.pollute(
        engine.repo.get(polluted_src["droplet_id"]),
        "low-confidence inference from an emotionally charged moment",
    )
    assert polluted.phase is Phase.POLLUTED
    assert polluted.meta.get("usable_for_generation") is False


def test_metric_interoperability_schema_and_verbs(engine):
    """Common schema/protocol round-trip losslessly; the 15 verbs are all present."""
    d = engine.absorb("Round-trip me through the schema.", context={"topic": "x"})
    stored = engine.repo.get(d["droplet_id"])
    assert stored is not None
    assert Droplet.from_dict(stored.to_dict(include_embedding=True)) == stored

    env = ProtocolEnvelope(operation="ABSORB", input={"content": "hi"})
    assert ProtocolEnvelope.from_dict(env.to_dict()) == env

    for verb in ALL_VERBS:
        assert callable(getattr(engine.verbs, verb)), f"missing verb {verb!r}"

    assert {p.value for p in STORABLE_PHASES} == {
        "liquid", "vapor", "cloud", "rain", "groundwater", "ice", "ocean", "polluted", "filtered",
    }


def test_metric_adaptation_preserves_history(engine):
    """Reconciling a conflict produces a filtered memory without erasing the originals."""
    old = engine.absorb("User prefers long detailed explanations.", context={"topic": "comms"})
    new = engine.absorb("User asks for concise answers today.", context={"topic": "comms"})

    engine.verbs.flow(engine.repo.get(old["droplet_id"]), [new["droplet_id"]], kind="contradictions")

    reconciled = engine.absorb(
        "User often prefers depth for complex topics but concise answers for simple tasks.",
        context={"topic": "comms"},
    )
    filtered = engine.verbs.filter(engine.repo.get(reconciled["droplet_id"]))
    assert filtered.phase is Phase.FILTERED

    # History is preserved: both original memories remain retrievable.
    assert engine.repo.get(old["droplet_id"]) is not None
    assert engine.repo.get(new["droplet_id"]) is not None
