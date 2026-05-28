"""Phase 4: HTTP boundary round-trip (FastAPI TestClient over a temp-DB engine).

Exercises the JSON surface end to end against a real Engine (real SQLite store +
stub intelligence + real governance/verbs), with no mocks: healthz/enums, absorb
(twice), recall (spec-shaped results), HQL GET (droplets), inspect, and the
FREEZE trust verb (which should land the droplet in ice/glacier).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from hydromemory.config import HydroConfig
from hydromemory.server import create_app


@pytest.fixture
def client(tmp_db_path: str) -> Iterator[TestClient]:
    config = HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    app = create_app(config)
    with TestClient(app) as c:  # `with` runs lifespan startup/shutdown.
        yield c


@pytest.fixture
def uninitialized_client(tmp_db_path: str) -> TestClient:
    """A client whose lifespan never ran, so ``app.state.engine`` is unset.

    Constructing ``TestClient`` *without* the ``with`` block skips lifespan
    startup; the engine/bus/grants dependency guards must then fail closed with a
    503 instead of an opaque ``AttributeError``. No engine is built, so there is
    nothing to close.
    """
    config = HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    return TestClient(create_app(config))


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["protocol"] == "HydroMemory"
    assert body["version"]


def test_enums_contract(client: TestClient) -> None:
    resp = client.get("/enums")
    assert resp.status_code == 200
    body = resp.json()
    expected_keys = {
        "phases",
        "storable_phases",
        "reservoirs",
        "visibilities",
        "retentions",
        "recall_modes",
        "operations",
        "verbs",
    }
    # The v1 keys must all be present; v2 (§9 bus + L4 grants) adds keys
    # additively (event_types, grant_statuses), so this is a superset check.
    assert set(body) >= expected_keys
    # Spot-check known canonical values and the storable subset relationship.
    assert "liquid" in body["phases"]
    assert "river" in body["phases"] and "river" not in body["storable_phases"]
    assert "working_stream" in body["reservoirs"]
    assert "glacier" in body["reservoirs"]
    assert body["visibilities"] == ["private", "shared", "public"]
    assert body["recall_modes"][0] == "literal"
    assert "mutate" in body["operations"]
    assert len(body["verbs"]) == 15
    assert "ABSORB" in body["verbs"] and "FORGET" in body["verbs"]


def test_absorb_recall_roundtrip(client: TestClient) -> None:
    # --- absorb #1 ----------------------------------------------------------
    r1 = client.post(
        "/absorb",
        json={
            "content": "User prefers architectural systems thinking over shallow summaries.",
            "source": "conversation",
            "context": {"topic": "AI memory systems", "session_type": "design"},
        },
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["stored"] is True
    assert d1["droplet_id"]
    assert d1["phase"] == "liquid"
    target_id = d1["droplet_id"]

    # --- absorb #2 ----------------------------------------------------------
    r2 = client.post(
        "/absorb",
        json={
            "content": "User values deep architecture, mechanisms, and executable frameworks.",
            "context": {"topic": "AI memory"},
        },
    )
    assert r2.status_code == 200
    assert r2.json()["stored"] is True

    # --- recall returns spec-shaped RecallResult dicts ----------------------
    rr = client.post(
        "/recall",
        json={"query": "architecture systems thinking", "agent": "assistant", "trust": "approved"},
    )
    assert rr.status_code == 200
    results = rr.json()["results"]
    assert results, "expected at least one recall hit through the HTTP boundary"
    first = results[0]
    assert set(first) >= {
        "mode",
        "surface_text",
        "internal_guidance",
        "show_to_user",
        "explanation",
        "droplet_id",
        "score",
    }
    assert isinstance(first["show_to_user"], bool)
    assert first["score"] > 0

    # --- inspect the absorbed droplet ---------------------------------------
    ins = client.get(f"/memory/{target_id}")
    assert ins.status_code == 200
    droplet = ins.json()
    assert droplet["id"] == target_id
    assert "architectural" in droplet["content"]
    assert droplet["phase"] == "liquid"


def test_hql_get_returns_droplets(client: TestClient) -> None:
    client.post(
        "/absorb",
        json={
            "content": "User values deep architecture and executable frameworks.",
            "context": {"topic": "AI memory"},
        },
    )
    resp = client.post("/hql", json={"query": 'GET memories WHERE phase="liquid"'})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results, "expected at least one droplet from HQL GET"
    # Each row is a serialized droplet (has the canonical droplet keys).
    row = results[0]
    assert {"id", "content", "phase", "reservoir", "state", "permissions"} <= set(row)


def test_inspect_missing_is_404(client: TestClient) -> None:
    resp = client.get("/memory/mem_doesnotexist")
    assert resp.status_code == 404


def test_freeze_then_inspect_shows_ice_glacier(client: TestClient) -> None:
    absorbed = client.post(
        "/absorb",
        json={"content": "A durable principle worth preserving.", "context": {"topic": "values"}},
    ).json()
    droplet_id = absorbed["droplet_id"]

    frozen = client.post("/freeze", json={"id": droplet_id, "agent": "user", "trust": "high_trust"})
    assert frozen.status_code == 200
    fbody = frozen.json()
    assert fbody["phase"] == "ice"
    assert fbody["reservoir"] == "glacier"

    # The change persisted: re-inspecting shows the frozen state.
    again = client.get(f"/memory/{droplet_id}").json()
    assert again["phase"] == "ice"
    assert again["reservoir"] == "glacier"


def test_drain_reduces_active_influence(client: TestClient) -> None:
    absorbed = client.post(
        "/absorb",
        json={"content": "An ephemeral note that should stop pulling.", "context": {"topic": "scratch"}},
    ).json()
    droplet_id = absorbed["droplet_id"]

    drained = client.post("/drain", json={"id": droplet_id})
    assert drained.status_code == 200
    body = drained.json()
    assert body["state"]["pressure"] == 0.0
    assert body["state"]["fluidity"] == 0.0


def test_forget_deletes_droplet(client: TestClient) -> None:
    absorbed = client.post(
        "/absorb",
        json={"content": "Delete me by user command.", "context": {"topic": "scratch"}},
    ).json()
    droplet_id = absorbed["droplet_id"]

    resp = client.post("/forget", json={"id": droplet_id, "agent": "user", "trust": "high_trust"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["operation"] == "FORGET"
    assert body["result"] is True
    assert body["outcome"]["deleted"] is True

    # The droplet is gone.
    assert client.get(f"/memory/{droplet_id}").status_code == 404


# --------------------------------------------------------------------------- #
# Dependency guard: engine-backed endpoints return 503 when lifespan didn't init
# --------------------------------------------------------------------------- #


def test_engine_endpoint_503_when_engine_uninitialized(uninitialized_client: TestClient) -> None:
    resp = uninitialized_client.post("/absorb", json={"content": "anything"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "engine not initialized"


def test_inspect_503_when_engine_uninitialized(uninitialized_client: TestClient) -> None:
    # The guard fires before the per-droplet 404 lookup.
    resp = uninitialized_client.get("/memory/mem_anything")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "engine not initialized"
