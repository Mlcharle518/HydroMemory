"""L4 — Sovereign Cognitive OS: the capability/consent grant protocol (Phase B2).

Scenario: a non-owner app requests scoped access to the user's memory; the owner
approves; :func:`~hydromemory.platform.grants.enforce_grant` then gates every
access — composing governance ``check_access`` AND an active grant (narrow-only).

The droplet lives in ``surface`` (owner ``"user"``) and the app agent has
``APPROVED`` trust, so the *base* ``check_access`` ALLOWS the READ — the grant
layer is therefore the thing being exercised (a base denial would mask it).

Lifecycle asserted end to end, with audit entries:

* no grant            -> DENIED ("no active grant ...") even though base allows;
* request -> approve  -> ALLOWED;
* revoke              -> DENIED again;
* an expired grant    -> DENIED (past expiry treated as inactive);
* owner / user-proxy  -> ALLOWED without any grant (the app-layer bypass).

NOTE (B1 contract): ``enforce_grant`` audits the deny paths and the
allow-after-grant path, but the user-proxy *bypass* returns the base decision
*before* the audit call — so the owner's allowed access is intentionally not
recorded by ``enforce_grant`` (it is the owner operating their own vault at L2,
audited there instead). The test asserts the four app-agent decisions.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from hydromemory.governance import AccessContext, AgentIdentity, Operation, TrustLevel
from hydromemory.platform.grants import GrantRequest, GrantStore, enforce_grant
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Permissions
from hydromemory.vault.audit import AuditLog

# A non-owner app. Surface requires APPROVED trust, so base check_access ALLOWS
# — the grant layer is what the scenario exercises.
APP_AGENT = AgentIdentity(name="calendar_app", trust_level=TrustLevel.APPROVED)
# The owner acting directly (user proxy); HIGH_TRUST clears every reservoir floor.
OWNER_AGENT = AgentIdentity(
    name="owner", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True
)


@pytest.fixture
def env(tmp_path):
    """A grant store + an audit log sharing one temp sqlite connection."""
    conn = sqlite3.connect(str(tmp_path / "grants.db"))
    conn.row_factory = sqlite3.Row
    store = GrantStore(conn)
    audit = AuditLog(conn)
    try:
        yield {"conn": conn, "store": store, "audit": audit}
    finally:
        conn.close()


def _surface_droplet(did: str = "s1", owner: str = "user") -> Droplet:
    return Droplet(
        id=did,
        content="a surface note",
        reservoir=Reservoir.SURFACE,
        permissions=Permissions(owner=owner),
    )


def _approve(store: GrantStore, *, app_id: str, owner: str, reservoirs, operations, expiry=None) -> str:
    req = GrantRequest(
        app_id=app_id,
        owner=owner,
        reservoirs=list(reservoirs),
        operations=list(operations),
        purpose="sync calendar",
        expiry=expiry,
    )
    store.request(req)
    store.approve(req.request_id, owner)
    return req.request_id


def test_l4_full_grant_lifecycle_with_audit(env):
    store, audit = env["store"], env["audit"]
    droplet = _surface_droplet()

    # 1) Before any grant: base check_access allows surface+APPROVED, but the
    #    grant layer denies a non-owner app with no active grant.
    d0 = enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store, audit=audit,
    )
    assert d0.allowed is False
    assert "no active grant" in (d0.denial_reason or "")

    # 2) Request -> approve a [surface],[READ] grant: now allowed.
    rid = _approve(
        store, app_id="calendar_app", owner="user",
        reservoirs=[Reservoir.SURFACE], operations=[Operation.READ],
    )
    d1 = enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store, audit=audit,
    )
    assert d1.allowed is True

    # 3) Revoke -> denied again.
    store.revoke(rid, "user")
    d2 = enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store, audit=audit,
    )
    assert d2.allowed is False

    # 4) A separately-approved but already-expired grant -> denied.
    _approve(
        store, app_id="calendar_app", owner="user",
        reservoirs=[Reservoir.SURFACE], operations=[Operation.READ],
        expiry=datetime.now(UTC) - timedelta(seconds=1),
    )
    d3 = enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store, audit=audit,
    )
    assert d3.allowed is False

    # Audit recorded all four app-agent decisions (deny, allow, deny, deny).
    app_entries = audit.query(actor="calendar_app")
    assert [e.allowed for e in app_entries] == [False, True, False, False]
    assert all(e.operation == "read" and e.droplet_id == "s1" for e in app_entries)
    assert all(e.detail is not None for e in app_entries)
    # The tamper-evident chain holds across the lifecycle.
    assert audit.verify_chain() is True


def test_l4_owner_user_proxy_bypasses_grant(env):
    store, audit = env["store"], env["audit"]
    droplet = _surface_droplet()

    # No grant exists, yet the owner (user proxy) is allowed — the L2 bypass.
    decision = enforce_grant(
        droplet, OWNER_AGENT, AccessContext(), Operation.READ,
        app_id=None, store=store, audit=audit,
    )
    assert decision.allowed is True
    # (Per the B1 contract, the user-proxy bypass returns before the audit call,
    # so enforce_grant records nothing for the owner here.)
    assert audit.query(actor="owner") == []


def test_l4_grant_narrowing_wrong_op_and_reservoir_denied(env):
    """A grant can only narrow: a mismatched op or reservoir is still denied."""
    store, audit = env["store"], env["audit"]
    droplet = _surface_droplet()

    # Grant covers [surface],[READ] only.
    _approve(
        store, app_id="calendar_app", owner="user",
        reservoirs=[Reservoir.SURFACE], operations=[Operation.READ],
    )

    # MUTATE (not in the grant's operations) -> denied.
    mutate = enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.MUTATE,
        app_id="calendar_app", store=store, audit=audit,
    )
    assert mutate.allowed is False

    # A groundwater droplet (not in the grant's reservoirs) -> the base
    # check_access already denies APPROVED trust on groundwater, so the grant
    # never even applies; either way the result is a denial.
    deep = Droplet(
        id="g1", content="deep", reservoir=Reservoir.GROUNDWATER,
        permissions=Permissions(owner="user"),
    )
    deep_dec = enforce_grant(
        deep, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store, audit=audit,
    )
    assert deep_dec.allowed is False


def test_l4_denied_base_check_access_is_not_resurrected_by_grant(env):
    """Grants never widen: a base denial stays denied even with a matching grant."""
    store, audit = env["store"], env["audit"]
    # Contaminated is filtration-agent-only: base check_access denies the app.
    droplet = Droplet(
        id="c1", content="bad", reservoir=Reservoir.CONTAMINATED,
        permissions=Permissions(owner="user"),
    )
    _approve(
        store, app_id="calendar_app", owner="user",
        reservoirs=[Reservoir.CONTAMINATED], operations=[Operation.READ],
    )
    decision = enforce_grant(
        droplet, APP_AGENT, AccessContext(), Operation.READ,
        app_id="calendar_app", store=store, audit=audit,
    )
    assert decision.allowed is False
    # The denial reason is the governance one, not a grant denial.
    assert "filtration" in (decision.denial_reason or "")
