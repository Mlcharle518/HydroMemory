"""HydroClient SDK — open-core build (memory + canonical only; ADR-0048).

In the open core, ``HydroClient`` drives the three memory verbs through the canonical surface;
the nine upper-layer verbs raise :class:`~hydromemory.sdk.SdkError` because their layer isn't
shipped.
"""
from __future__ import annotations

import pytest

from hydromemory.canonical.verbs import CanonicalVerb
from hydromemory.config import HydroConfig
from hydromemory.sdk import HydroClient, SdkError


@pytest.fixture
def client(tmp_path):
    with HydroClient(HydroConfig(db_path=str(tmp_path / "h.db"), vector_dim=64)) as hc:
        yield hc


def test_client_absorbs_and_canonicalizes(client):
    d = client.absorb("hello, open core")
    assert d.id
    env = client.canonical(d)
    assert env["object_type"] == "memory"
    assert client.validate(d) == []


def test_which_verbs_reports_memory_only(client):
    avail = client.which_verbs()
    # Memory verbs resolve in the open core; upper-layer verbs don't.
    for v in (CanonicalVerb.ABSORB, CanonicalVerb.RECALL, CanonicalVerb.FORGET):
        assert avail[v.value] is True, f"{v.value} should resolve"
    for v in (CanonicalVerb.SENSE, CanonicalVerb.ANCHOR, CanonicalVerb.FORM_INTENT,
              CanonicalVerb.JUDGE, CanonicalVerb.PLAN, CanonicalVerb.ACT,
              CanonicalVerb.REFLECT, CanonicalVerb.INTEGRATE, CanonicalVerb.SUPERSEDE):
        assert avail[v.value] is False, f"{v.value} should not resolve"


def test_upper_layer_verb_raises_sdk_error(client):
    with pytest.raises(SdkError):
        client.verb(CanonicalVerb.JUDGE)


def test_events_requires_cognitive_bus_in_open_core(client):
    # The cognitive bus is wired by the engine only when integrate is enabled (commercial layer).
    # In the open core that's absent, so events() raises SdkError with a clear message rather than
    # silently failing.
    with pytest.raises(SdkError):
        client.events(subscriber="user")
