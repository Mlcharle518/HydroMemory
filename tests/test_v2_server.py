"""v2 Phase B2: the §9 bus + L4 grant + L1 app HTTP surface (FastAPI TestClient).

Exercises the *additive* v2 endpoints over a real Engine wired to a live
:class:`~hydromemory.bus.EventBus` and :class:`~hydromemory.platform.grants.GrantStore`
(temp-DB, stub intelligence, real governance):

* ``GET /enums`` now carries ``event_types`` + ``grant_statuses``.
* ``WS /events/subscribe`` -> ``POST /absorb`` delivers an ``absorbed`` frame
  cross-"process" via the bus's bounded queue seam (Starlette's TestClient runs
  the WS on the same loop, so this proves the queue/unsubscribe wiring).
* ``POST /events`` returns a delivery count and reaches a live subscriber.
* the L4 grant lifecycle (request -> pending, approve -> approved, list).
* ``POST /apps`` registers an app-scoped handle.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from hydromemory.bus.events import EventType
from hydromemory.config import HydroConfig
from hydromemory.platform.grants import GrantStatus
from hydromemory.server import create_app


@pytest.fixture
def client(tmp_db_path: str) -> Iterator[TestClient]:
    config = HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    app = create_app(config)
    with TestClient(app) as c:  # `with` runs lifespan startup/shutdown.
        yield c


@pytest.fixture
def uninitialized_client(tmp_db_path: str) -> TestClient:
    """A client whose lifespan never ran, so ``app.state.bus`` / ``.grants`` are
    unset; the v2 bus/grant dependency guards must fail closed with a 503.
    """
    config = HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")
    return TestClient(create_app(config))


# --------------------------------------------------------------------------- #
# /enums now carries the v2 enums
# --------------------------------------------------------------------------- #


def test_enums_include_event_types_and_grant_statuses(client: TestClient) -> None:
    body = client.get("/enums").json()
    assert "event_types" in body
    assert "grant_statuses" in body
    assert body["event_types"] == [e.value for e in EventType]
    assert body["grant_statuses"] == [s.value for s in GrantStatus]
    # Spot-check canonical values.
    assert "absorbed" in body["event_types"]
    assert "transformed" in body["event_types"]
    assert body["grant_statuses"] == ["pending", "approved", "denied", "revoked", "expired"]


# --------------------------------------------------------------------------- #
# WebSocket round-trip: absorb -> 'absorbed' frame on the socket
# --------------------------------------------------------------------------- #


def test_ws_absorb_delivers_absorbed_event(client: TestClient) -> None:
    with client.websocket_connect("/events/subscribe?agent=assistant&trust=high_trust") as ws:
        absorbed = client.post(
            "/absorb",
            json={
                "content": "User prefers architectural systems thinking.",
                "context": {"topic": "AI memory systems"},
            },
        ).json()
        assert absorbed["stored"] is True

        frame = ws.receive_json()
        assert frame["type"] == EventType.ABSORBED.value
        assert frame["droplet_id"] == absorbed["droplet_id"]
        # The frame is a full MemoryEvent.to_dict().
        assert set(frame) >= {"type", "actor", "droplet_id", "app_id", "timestamp", "payload"}
        assert frame["actor"] == "server"


def test_ws_topic_filter_only_delivers_matching(client: TestClient) -> None:
    # Subscribe to RECALLED only. A non-matching publish (DISTILLED) must be
    # filtered out; only the matching RECALLED frame should arrive. We use
    # droplet-less POST /events so the bus permission gate is skipped and we are
    # exercising the topic filter in isolation.
    with client.websocket_connect("/events/subscribe?agent=assistant&topics=recalled") as ws:
        # This one does not match the topic filter and must be dropped.
        dropped = client.post("/events", json={"type": EventType.DISTILLED.value})
        assert dropped.json()["delivered"] == 0
        # This one matches.
        matched = client.post(
            "/events",
            json={"type": EventType.RECALLED.value, "payload": {"seq": 1}},
        )
        assert matched.json()["delivered"] == 1

        frame = ws.receive_json()
        assert frame["type"] == EventType.RECALLED.value
        assert frame["payload"]["seq"] == 1


# --------------------------------------------------------------------------- #
# POST /events: explicit publish reaches a live subscriber
# --------------------------------------------------------------------------- #


def test_post_events_delivers_to_subscriber(client: TestClient) -> None:
    with client.websocket_connect("/events/subscribe?agent=assistant") as ws:
        resp = client.post(
            "/events",
            json={"type": EventType.RECALLED.value, "payload": {"note": "manual"}},
        )
        assert resp.status_code == 200
        assert resp.json()["delivered"] == 1
        frame = ws.receive_json()
        assert frame["type"] == EventType.RECALLED.value
        assert frame["payload"]["note"] == "manual"


def test_post_events_no_subscribers_delivers_zero(client: TestClient) -> None:
    resp = client.post("/events", json={"type": EventType.RECALLED.value})
    assert resp.status_code == 200
    assert resp.json()["delivered"] == 0


# --------------------------------------------------------------------------- #
# L4 grant lifecycle over HTTP
# --------------------------------------------------------------------------- #


def test_grant_lifecycle_request_approve_list(client: TestClient) -> None:
    # --- request -> pending --------------------------------------------------
    requested = client.post(
        "/grants/request",
        json={
            "app_id": "calendar",
            "owner": "user",
            "reservoirs": ["surface", "groundwater"],
            "operations": ["read", "use_for_generation"],
            "purpose": "schedule-aware reminders",
        },
    )
    assert requested.status_code == 200
    grant = requested.json()
    request_id = grant["request_id"]
    assert grant["status"] == GrantStatus.PENDING.value
    assert grant["app_id"] == "calendar"
    assert set(grant["reservoirs"]) == {"surface", "groundwater"}
    assert set(grant["operations"]) == {"read", "use_for_generation"}
    assert grant["granted_at"] is None

    # --- approve -> approved (stamps granted_at) -----------------------------
    approved = client.post(f"/grants/{request_id}/approve", json={"owner": "user"})
    assert approved.status_code == 200
    abody = approved.json()
    assert abody["status"] == GrantStatus.APPROVED.value
    assert abody["granted_at"] is not None

    # --- list shows the (now approved) grant ---------------------------------
    listed = client.get("/grants", params={"owner": "user"})
    assert listed.status_code == 200
    grants = listed.json()["grants"]
    assert len(grants) == 1
    assert grants[0]["request_id"] == request_id
    assert grants[0]["status"] == GrantStatus.APPROVED.value


def test_grant_deny_and_revoke(client: TestClient) -> None:
    req = client.post(
        "/grants/request",
        json={
            "app_id": "ads",
            "owner": "user",
            "reservoirs": ["surface"],
            "operations": ["read"],
            "purpose": "targeting",
        },
    ).json()
    denied = client.post(f"/grants/{req['request_id']}/deny", json={"owner": "user"})
    assert denied.status_code == 200
    assert denied.json()["status"] == GrantStatus.DENIED.value

    # A second grant we approve then revoke.
    req2 = client.post(
        "/grants/request",
        json={
            "app_id": "fitness",
            "owner": "user",
            "reservoirs": ["surface"],
            "operations": ["read"],
            "purpose": "coaching",
        },
    ).json()
    client.post(f"/grants/{req2['request_id']}/approve", json={"owner": "user"})
    revoked = client.post(f"/grants/{req2['request_id']}/revoke", json={"owner": "user"})
    assert revoked.status_code == 200
    assert revoked.json()["status"] == GrantStatus.REVOKED.value


def test_grant_wrong_owner_is_403(client: TestClient) -> None:
    req = client.post(
        "/grants/request",
        json={
            "app_id": "calendar",
            "owner": "alice",
            "reservoirs": ["surface"],
            "operations": ["read"],
            "purpose": "p",
        },
    ).json()
    resp = client.post(f"/grants/{req['request_id']}/approve", json={"owner": "mallory"})
    assert resp.status_code == 403


def test_grant_unknown_request_is_404(client: TestClient) -> None:
    resp = client.post("/grants/does_not_exist/approve", json={"owner": "user"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# L1 app registration
# --------------------------------------------------------------------------- #


def test_register_app(client: TestClient) -> None:
    resp = client.post("/apps", json={"app_id": "journal", "owner": "user"})
    assert resp.status_code == 200
    assert resp.json() == {"app_id": "journal", "owner": "user"}


def test_register_app_defaults_owner_to_user(client: TestClient) -> None:
    resp = client.post("/apps", json={"app_id": "notes"})
    assert resp.status_code == 200
    assert resp.json() == {"app_id": "notes", "owner": "user"}


# --------------------------------------------------------------------------- #
# Dependency guards: bus + grant endpoints 503 when lifespan didn't init
# --------------------------------------------------------------------------- #


def test_events_503_when_bus_uninitialized(uninitialized_client: TestClient) -> None:
    resp = uninitialized_client.post("/events", json={"type": EventType.RECALLED.value})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "bus not initialized"


def test_grants_list_503_when_grant_store_uninitialized(uninitialized_client: TestClient) -> None:
    resp = uninitialized_client.get("/grants", params={"owner": "user"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "grant store not initialized"


def test_grant_request_503_when_grant_store_uninitialized(uninitialized_client: TestClient) -> None:
    resp = uninitialized_client.post(
        "/grants/request",
        json={
            "app_id": "calendar",
            "owner": "user",
            "reservoirs": ["surface"],
            "operations": ["read"],
            "purpose": "p",
        },
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "grant store not initialized"
