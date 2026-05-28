"""HTTP boundary over the HydroMemory Engine (Phase 4).

A small JSON/HTTP surface that exposes the fully-wired :class:`~hydromemory.engine.Engine`
(see :mod:`hydromemory.engine`) so non-Python callers — notably the thin TypeScript
client in ``clients/ts`` — can absorb experiences, recall memory, run HQL, inspect
droplets, and drive the trust verbs (FREEZE/DRAIN/FORGET).

Design notes:

* The engine is built once from :meth:`HydroConfig.from_env` (DB path from
  ``$HYDRO_DB_PATH``) and held in ``app.state``; it is closed on shutdown.
* Governance is computed **server-side**. Clients pass an optional ``agent`` name
  and ``trust`` level; the server constructs the :class:`AgentIdentity` /
  :class:`AccessContext` and surfaces the resulting decision read-only. Clients
  cannot smuggle in a decision.
* Every response is plain JSON: droplets via :meth:`Droplet.to_dict`, recall
  results as flat dicts, and protocol responses via :meth:`ProtocolResponse.to_dict`.
* ``GET /enums`` is the canonical contract the TS client mirrors (and the parity
  test pins): the exact string values of every protocol enum.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import importlib
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from hydromemory import __version__
from hydromemory.bus import EventBus, EventType, MemoryEvent
from hydromemory.config import HydroConfig
from hydromemory.engine import Engine, build_engine
from hydromemory.governance import AccessContext, AgentIdentity, Operation, TrustLevel
from hydromemory.platform.apps import register_app
from hydromemory.platform.grants import Grant, GrantRequest, GrantStatus, GrantStore
from hydromemory.protocol import PROTOCOL_NAME, PROTOCOL_VERSION, ProtocolResponse
from hydromemory.recall import RecallMode, RecallResult
from hydromemory.reservoirs import Reservoir, normalize_reservoir
from hydromemory.schema import (
    STORABLE_PHASES,
    Droplet,
    Phase,
    Retention,
    Visibility,
)


# HydroIntent is OPTIONAL: in the open-core ("HydroMemory") distribution its package is absent.
# Loading defensively (same lazy pattern as canonical/projection.py — ADR-0047/0051/0052) lets
# server.py ship in the open core; the /intents/* endpoints return 503 when the layer is
# unavailable (every handler calls `_intents(app)` first, which raises 503 when `engine.intents`
# is None).
def _opt_import(module: str, name: str) -> Any:
    try:
        return getattr(importlib.import_module(module), name)
    except ImportError:  # pragma: no cover - open-core build
        return None


Conflict = _opt_import("hydromemory.hydrointent.schema", "Conflict")
Intent = _opt_import("hydromemory.hydrointent.schema", "Intent")
IntentStatus = _opt_import("hydromemory.hydrointent.schema", "IntentStatus")
_INTENTS_AVAILABLE = Intent is not None


# --------------------------------------------------------------------------- #
# Request bodies (thin; all governance is recomputed server-side).
# --------------------------------------------------------------------------- #


class AbsorbRequest(BaseModel):
    content: str
    source: str = "conversation"
    context: dict[str, Any] | None = None


class RecallRequest(BaseModel):
    query: str
    agent: str | None = None
    trust: str | None = None
    context: dict[str, Any] | None = None


class HQLRequest(BaseModel):
    query: str


class FreezeRequest(BaseModel):
    id: str
    agent: str | None = None
    trust: str | None = None
    consent: bool = False
    thaw: bool = False


class DrainRequest(BaseModel):
    id: str


class ForgetRequest(BaseModel):
    id: str
    agent: str | None = None
    trust: str | None = None


class EventRequest(BaseModel):
    """A client-published bus event (POST /events).

    ``type`` is an :class:`EventType` value; the ``actor`` is recomputed
    server-side from ``agent``/``trust`` like every other endpoint.
    """

    type: str
    droplet_id: str | None = None
    app_id: str | None = None
    payload: dict[str, Any] | None = None
    agent: str | None = None
    trust: str | None = None


class GrantRequestBody(BaseModel):
    app_id: str
    owner: str
    reservoirs: list[str]
    operations: list[str]
    purpose: str
    expiry: str | None = None  # ISO-8601 datetime


class GrantDecisionBody(BaseModel):
    """Owner-only transition body for approve/deny/revoke."""

    owner: str


class AppRegisterBody(BaseModel):
    app_id: str
    owner: str | None = None


# --------------------------------------------------------------------------- #
# HydroIntent request bodies (PRD §16; governance recomputed server-side).
# --------------------------------------------------------------------------- #


class IntentProposeBody(BaseModel):
    statement: str
    intent_type: str = "general"
    scope: str = "session"
    domain: str = ""
    source_memories: list[str] | None = None
    constraints: list[str] | None = None
    desired_future_state: str = ""
    source: str = "user"
    priority: float = 0.5
    urgency: float = 0.4
    confidence: float = 0.6
    sensitivity: float = 0.0


class IntentDetectBody(BaseModel):
    """Distill an intent from explicit source droplet ids OR a recall ``query``."""

    source_memories: list[str] | None = None
    query: str | None = None
    statement: str | None = None
    intent_type: str = "general"
    scope: str = "session"
    domain: str = ""
    min_support: int = 2


class IntentGateBody(BaseModel):
    """Shared body for governance-gated single-intent transitions."""

    agent: str | None = None
    trust: str | None = None
    consent: bool = False
    reason: str | None = None


class IntentConflictBody(BaseModel):
    agent: str | None = None
    trust: str | None = None
    threshold: float = 0.6
    tension: str = ""
    recommended_resolution: str = ""


class IntentResolveBody(BaseModel):
    agent: str | None = None
    trust: str | None = None
    consent: bool = False
    keep: bool = True


class IntentMergeBody(BaseModel):
    ids: list[str]
    statement: str | None = None
    intent_type: str = "general"
    scope: str | None = None


class IntentSplitBody(BaseModel):
    parts: list[str]
    scope: str | None = None


class IntentHandoffBody(BaseModel):
    agent: str | None = None
    trust: str | None = None
    consent: bool = False
    to: str = "judgment"  # "judgment" | "plan"


class IntentRetireBody(BaseModel):
    outcome: str = "fulfilled"  # "fulfilled" | "abandoned"
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _trust_level(trust: str | None) -> TrustLevel:
    """Coerce a client-supplied trust string to a :class:`TrustLevel`.

    Unknown / missing values default to ``APPROVED`` (the same default the CLI and
    :meth:`Engine.recall` use).
    """
    if trust is None:
        return TrustLevel.APPROVED
    try:
        return TrustLevel(str(trust).lower())
    except ValueError:
        return TrustLevel.APPROVED


def _agent(name: str | None, trust: str | None, *, is_user_proxy: bool = False) -> AgentIdentity:
    return AgentIdentity(
        name=name or "assistant",
        trust_level=_trust_level(trust),
        is_user_proxy=is_user_proxy,
    )


def _recall_result_to_dict(result: RecallResult) -> dict[str, Any]:
    """Flatten a :class:`RecallResult` (dataclass) into a JSON-safe dict."""
    return {
        "mode": result.mode.value,
        "surface_text": result.surface_text,
        "internal_guidance": result.internal_guidance,
        "show_to_user": result.show_to_user,
        "explanation": result.explanation,
        "droplet_id": result.droplet_id,
        "score": result.score,
        "meta": dict(result.meta),
    }


def _serialize_hql(obj: Any) -> Any:
    """Serialize an HQL execution result.

    HQL ``execute`` returns one of: a list of :class:`Droplet` (GET), a list of
    :class:`RecallResult` (PRECIPITATE with recall wired), a single
    :class:`Droplet` (DISTILL), ``None`` (empty DISTILL), or the raw op ``dict``
    (PRECIPITATE without recall).
    """
    if obj is None:
        return None
    if isinstance(obj, Droplet):
        return obj.to_dict()
    if isinstance(obj, RecallResult):
        return _recall_result_to_dict(obj)
    if isinstance(obj, list):
        return [_serialize_hql(item) for item in obj]
    if isinstance(obj, dict):
        return obj
    # Defensive fallback for any other dataclass-shaped result.
    try:
        return asdict(obj)
    except TypeError:
        return obj


def _engine(app: FastAPI) -> Engine:
    engine = getattr(app.state, "engine", None)
    if engine is None:  # pragma: no cover - guarded by lifespan
        raise HTTPException(status_code=503, detail="engine not initialized")
    return engine


def _bus(app: FastAPI) -> EventBus:
    bus = getattr(app.state, "bus", None)
    if bus is None:  # pragma: no cover - guarded by lifespan
        raise HTTPException(status_code=503, detail="bus not initialized")
    return bus


def _grants(app: FastAPI) -> GrantStore:
    grants = getattr(app.state, "grants", None)
    if grants is None:  # pragma: no cover - guarded by lifespan
        raise HTTPException(status_code=503, detail="grant store not initialized")
    return grants


def _intents(app: FastAPI) -> Any:
    """The engine's ``IntentVerbs`` surface, or 503 when the layer is disabled."""
    intents = getattr(_engine(app), "intents", None)
    if intents is None:
        raise HTTPException(status_code=503, detail="HydroIntent layer is not enabled")
    return intents


def _get_intent(intents: Any, intent_id: str) -> Any:
    # Annotated as Any (not Intent) because Intent is loaded optionally via _opt_import — it's a
    # runtime value, not a type, so mypy can't use it as a type annotation.
    intent = intents.intent_repo.get(intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail=f"intent {intent_id!r} not found")
    return intent


def _conflict_to_dict(conflict: Any) -> dict[str, Any] | None:
    return conflict.to_dict() if conflict is not None else None


def _grant_conn(engine: Engine, config: HydroConfig) -> sqlite3.Connection:
    """Resolve the SQLite connection the :class:`GrantStore` should use.

    ``engine.repo`` is a :class:`SqliteDropletRepository` whose ``._conn`` lives
    on the event-loop thread (the same thread the async handlers run on), so the
    grant store shares it safely. If the repo has no ``_conn`` (a non-SQLite repo
    in some future config), open a fresh connection from ``config.db_path``.
    """
    conn = getattr(engine.repo, "_conn", None)
    if isinstance(conn, sqlite3.Connection):
        return conn
    return sqlite3.connect(config.db_path)  # pragma: no cover - non-SQLite fallback


def _parse_expiry(value: str | None) -> datetime | None:
    """Parse a client ISO-8601 expiry string; ``None`` / blank -> ``None``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid expiry datetime: {value!r}") from exc


def _grant_to_dict(grant: Grant) -> dict[str, Any]:
    """Serialize a :class:`Grant` to JSON (enum ``.value``s, ISO datetimes)."""
    return {
        "request_id": grant.request_id,
        "app_id": grant.app_id,
        "owner": grant.owner,
        "reservoirs": [r.value for r in grant.reservoirs],
        "operations": [o.value for o in grant.operations],
        "purpose": grant.purpose,
        "status": grant.status.value,
        "granted_at": grant.granted_at.isoformat() if grant.granted_at is not None else None,
        "expiry": grant.expiry.isoformat() if grant.expiry is not None else None,
    }


def _grant_op(store: GrantStore, fn: str, request_id: str, owner: str) -> dict[str, Any]:
    """Run an owner-only grant transition, mapping store errors to HTTP codes."""
    method = getattr(store, fn)
    try:
        grant = method(request_id, owner)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _grant_to_dict(grant)


def _enums_payload() -> dict[str, list[str]]:
    """The canonical enum contract the TS client mirrors (and the parity test pins)."""
    return {
        "phases": [p.value for p in Phase],
        "storable_phases": [p.value for p in Phase if p in STORABLE_PHASES],
        "reservoirs": [r.value for r in Reservoir],
        "visibilities": [v.value for v in Visibility],
        "retentions": [r.value for r in Retention],
        "recall_modes": [m.value for m in RecallMode],
        "operations": [o.value for o in Operation],
        "verbs": list(_API_VERBS),
        "event_types": [e.value for e in EventType],
        "grant_statuses": [s.value for s in GrantStatus],
        "intent_statuses": [s.value for s in IntentStatus] if _INTENTS_AVAILABLE else [],
        "intent_verbs": list(_INTENT_VERBS),
    }


# The HydroIntent protocol verbs (PRD §16), in canonical order.
_INTENT_VERBS: tuple[str, ...] = (
    "DETECT_INTENT",
    "PROPOSE_INTENT",
    "ACTIVATE_INTENT",
    "DEFER_INTENT",
    "SUPPRESS_INTENT",
    "DETECT_CONFLICTS",
    "RESOLVE_CONFLICT",
    "MERGE_INTENTS",
    "SPLIT_INTENT",
    "QUERY_INTENT",
    "PRIORITIZE_INTENTS",
    "HANDOFF_TO_JUDGMENT",
    "HANDOFF_TO_PLAN",
    "RETIRE_INTENT",
    "DELETE_INTENT",
)


# The 15 HydroMemory API verbs (PRD §5.7, §6), in canonical order.
_API_VERBS: tuple[str, ...] = (
    "ABSORB",
    "FLOW",
    "EVAPORATE",
    "CONDENSE",
    "PRECIPITATE",
    "INFILTRATE",
    "FREEZE",
    "MELT",
    "FILTER",
    "POLLUTE",
    "DISTILL",
    "IRRIGATE",
    "DRAIN",
    "ARCHIVE",
    "FORGET",
)


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #


def create_app(config: HydroConfig | None = None) -> FastAPI:
    """Build the FastAPI app over a fully-wired Engine.

    ``config`` lets tests inject a temp-DB config; in production it is ``None`` and
    the engine is built from :meth:`HydroConfig.from_env`.
    """

    resolved_config = config or HydroConfig.from_env()
    # The platform server IS the HydroIntent "Intent Bus" (PRD §15), so it always
    # exposes the intent surface (engine.intents). Force the flag on a copy so we
    # never mutate the caller's config (ADR-0042; default-off elsewhere).
    # In the full HydroCognitive build the server force-enables the Intent Bus (PRD §15). In the
    # open-core build the layer is absent; leave the flag alone and the /intents/* endpoints will
    # 503 when called.
    if _INTENTS_AVAILABLE and not resolved_config.intents_enabled:
        resolved_config = dataclasses.replace(resolved_config, intents_enabled=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(resolved_config)
        # Wire the §9 memory event bus: the engine's verbs + capture/recall
        # pipeline now emit lifecycle events onto ``bus``. Permission gating uses
        # the engine's repo so a subscriber never receives an event about a
        # droplet it cannot READ.
        bus = EventBus(repo=engine.repo)
        engine.attach_bus(bus, actor="server")
        # The grant store shares the engine's SQLite connection (same loop thread).
        grants = GrantStore(_grant_conn(engine, resolved_config))
        app.state.engine = engine
        app.state.bus = bus
        app.state.grants = grants
        try:
            yield
        finally:
            engine.close()

    app = FastAPI(title="HydroMemory", version=__version__, lifespan=lifespan)

    # NOTE: handlers are ``async def`` on purpose. The engine holds a single
    # SQLite connection created during lifespan startup (on the event-loop
    # thread); SQLite forbids cross-thread reuse. Async handlers run on that same
    # event-loop thread, whereas plain ``def`` handlers would be dispatched to a
    # worker threadpool and trip ``check_same_thread``. The engine calls are
    # fast/local (stub backend + local SQLite), so running them inline is fine
    # for this reference server.

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "protocol": PROTOCOL_NAME,
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
        }

    @app.get("/enums")
    async def enums() -> dict[str, list[str]]:
        return _enums_payload()

    @app.post("/absorb")
    async def absorb(req: AbsorbRequest) -> dict[str, Any]:
        engine = _engine(app)
        return engine.absorb(req.content, source=req.source, context=req.context or {})

    @app.post("/recall")
    async def recall(req: RecallRequest) -> dict[str, Any]:
        engine = _engine(app)
        agent = _agent(req.agent, req.trust)
        results = engine.recall(req.query, agent=agent, context=req.context or {})
        return {"results": [_recall_result_to_dict(r) for r in results]}

    @app.post("/hql")
    async def hql(req: HQLRequest) -> dict[str, Any]:
        engine = _engine(app)
        result = engine.hql(req.query)
        serialized = _serialize_hql(result)
        if isinstance(serialized, list):
            return {"results": serialized}
        return {"results": [serialized] if serialized is not None else []}

    @app.get("/memory/{droplet_id}")
    async def inspect(droplet_id: str) -> dict[str, Any]:
        engine = _engine(app)
        droplet = engine.repo.get(droplet_id)
        if droplet is None:
            raise HTTPException(status_code=404, detail=f"droplet {droplet_id!r} not found")
        return droplet.to_dict()

    @app.post("/freeze")
    async def freeze(req: FreezeRequest) -> dict[str, Any]:
        engine = _engine(app)
        droplet = engine.repo.get(req.id)
        if droplet is None:
            raise HTTPException(status_code=404, detail=f"droplet {req.id!r} not found")
        agent = _agent(req.agent, req.trust, is_user_proxy=True)
        context = AccessContext(consent_granted=req.consent, thaw_granted=req.thaw)
        frozen = engine.verbs.freeze(droplet, agent=agent, context=context)
        return frozen.to_dict()

    @app.post("/drain")
    async def drain(req: DrainRequest) -> dict[str, Any]:
        engine = _engine(app)
        droplet = engine.repo.get(req.id)
        if droplet is None:
            raise HTTPException(status_code=404, detail=f"droplet {req.id!r} not found")
        drained = engine.verbs.drain(droplet)
        return drained.to_dict()

    @app.post("/forget")
    async def forget(req: ForgetRequest) -> dict[str, Any]:
        engine = _engine(app)
        droplet = engine.repo.get(req.id)
        if droplet is None:
            raise HTTPException(status_code=404, detail=f"droplet {req.id!r} not found")
        # FORGET acts on the user's behalf (user-proxy) so the owner can delete.
        agent = _agent(req.agent, req.trust, is_user_proxy=True)
        context = AccessContext()
        response: ProtocolResponse = engine.verbs.forget(droplet, agent=agent, context=context)
        return response.to_dict()

    # ----------------------------------------------------------------- #
    # §9 memory event bus
    # ----------------------------------------------------------------- #

    @app.post("/events")
    async def publish_event(req: EventRequest) -> dict[str, Any]:
        """Publish a :class:`MemoryEvent` onto the bus; return the delivery count.

        The ``actor`` is the server-computed agent name (clients cannot forge a
        privileged actor any more than they can forge a governance decision).
        """
        bus = _bus(app)
        agent = _agent(req.agent, req.trust)
        event = MemoryEvent(
            type=req.type,
            actor=agent.name,
            droplet_id=req.droplet_id,
            app_id=req.app_id,
            payload=dict(req.payload or {}),
        )
        delivered = bus.publish(event)
        return {"delivered": delivered}

    @app.websocket("/events/subscribe")
    async def subscribe_events(websocket: WebSocket) -> None:
        """Stream live bus events to a WebSocket client.

        Query params: ``agent`` / ``trust`` (the subscriber identity used for the
        bus permission gate) and an optional comma-separated ``topics`` filter.
        The bus pushes events into a bounded :class:`asyncio.Queue` (drop-oldest
        when full, so a slow client never blocks ``publish``); this coroutine
        drains the queue and forwards each event as a JSON frame.

        Two tasks run concurrently: a *pump* that awaits ``queue.get()`` and sends
        each event, and a *receiver* that awaits ``websocket.receive()`` purely to
        notice a client disconnect (the pump never reads, so without this the
        close handshake would never be observed). When either settles — a
        disconnect, or a send to a closed socket — the other is cancelled and the
        subscription is torn down.
        """
        bus = _bus(app)
        params = websocket.query_params
        topics_raw = params.get("topics")
        topics: frozenset[str] | None = None
        if topics_raw:
            parsed = {t.strip() for t in topics_raw.split(",") if t.strip()}
            if parsed:
                topics = frozenset(parsed)
        identity = _agent(params.get("agent"), params.get("trust"))

        await websocket.accept()
        queue: asyncio.Queue[MemoryEvent] = asyncio.Queue(maxsize=100)
        sub = bus.subscribe(topics=topics, handler=queue, subscriber=identity)

        async def pump() -> None:
            while True:
                event = await queue.get()
                await websocket.send_json(event.to_dict())

        async def watch_disconnect() -> None:
            # Drain inbound frames until the client goes away. ``receive()``
            # returns a ``websocket.disconnect`` message (and raises
            # WebSocketDisconnect for the higher-level receive_* helpers); either
            # way we stop on the first disconnect signal — calling ``receive()``
            # again after a disconnect raises RuntimeError.
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
            except WebSocketDisconnect:
                return

        pump_task = asyncio.create_task(pump())
        watch_task = asyncio.create_task(watch_disconnect())
        try:
            done, pending = await asyncio.wait(
                {pump_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            # Surface a non-cancellation error from the completed task (but a
            # client disconnect mid-send is expected and swallowed).
            for task in done:
                with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                    task.result()
        finally:
            bus.unsubscribe(sub)
            for task in (pump_task, watch_task):
                if not task.done():
                    task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(pump_task, watch_task, return_exceptions=True)

    # ----------------------------------------------------------------- #
    # L4 capability/consent grant protocol
    # ----------------------------------------------------------------- #

    @app.post("/grants/request")
    async def grant_request(req: GrantRequestBody) -> dict[str, Any]:
        store = _grants(app)
        try:
            reservoirs = [normalize_reservoir(r) for r in req.reservoirs]
            operations = [Operation(o) for o in req.operations]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        grant_req = GrantRequest(
            app_id=req.app_id,
            owner=req.owner,
            reservoirs=reservoirs,
            operations=operations,
            purpose=req.purpose,
            expiry=_parse_expiry(req.expiry),
        )
        return _grant_to_dict(store.request(grant_req))

    @app.post("/grants/{request_id}/approve")
    async def grant_approve(request_id: str, req: GrantDecisionBody) -> dict[str, Any]:
        return _grant_op(_grants(app), "approve", request_id, req.owner)

    @app.post("/grants/{request_id}/deny")
    async def grant_deny(request_id: str, req: GrantDecisionBody) -> dict[str, Any]:
        return _grant_op(_grants(app), "deny", request_id, req.owner)

    @app.post("/grants/{request_id}/revoke")
    async def grant_revoke(request_id: str, req: GrantDecisionBody) -> dict[str, Any]:
        return _grant_op(_grants(app), "revoke", request_id, req.owner)

    @app.get("/grants")
    async def grant_list(owner: str) -> dict[str, Any]:
        store = _grants(app)
        return {"grants": [_grant_to_dict(g) for g in store.list(owner)]}

    # ----------------------------------------------------------------- #
    # L1 app registration
    # ----------------------------------------------------------------- #

    @app.post("/apps")
    async def register_app_endpoint(req: AppRegisterBody) -> dict[str, Any]:
        engine = _engine(app)
        # ``register_app`` reads the bus / grant store / vault off the engine;
        # expose the live ones so the handle it returns is wired to this server's
        # bus + grant store (and the engine's repo as its scoped vault) rather
        # than throwaway in-memory defaults.
        engine.bus = _bus(app)  # type: ignore[attr-defined]
        engine.grant_store = _grants(app)  # type: ignore[attr-defined]
        engine.vault = engine.repo  # type: ignore[attr-defined]
        app_memory = register_app(engine, req.app_id, req.owner or "user")
        return {"app_id": app_memory.app_id, "owner": app_memory.owner}

    # ----------------------------------------------------------------- #
    if _INTENTS_AVAILABLE:
        # HydroIntent — the Intent Bus surface (PRD §15/§16). Governance is
        # recomputed server-side from agent/trust/consent, exactly like memory.
        # ----------------------------------------------------------------- #

        @app.post("/intents/propose")
        async def intent_propose(req: IntentProposeBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = intents.propose_intent(
                req.statement,
                source_memories=req.source_memories,
                intent_type=req.intent_type,
                scope=req.scope,
                domain=req.domain,
                constraints=req.constraints,
                desired_future_state=req.desired_future_state,
                source=req.source,
                priority=req.priority,
                urgency=req.urgency,
                confidence=req.confidence,
                sensitivity=req.sensitivity,
            )
            return intent.to_dict()

        @app.post("/intents/detect")
        async def intent_detect(req: IntentDetectBody) -> dict[str, Any]:
            engine = _engine(app)
            intents = _intents(app)
            source = None
            if req.source_memories:
                source = [d for d in (engine.repo.get(m) for m in req.source_memories) if d is not None]
            try:
                intent = intents.detect_intent(
                    source,
                    query=req.query,
                    statement=req.statement,
                    intent_type=req.intent_type,
                    scope=req.scope,
                    domain=req.domain,
                    min_support=req.min_support,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return intent.to_dict()

        @app.post("/intents/merge")
        async def intent_merge(req: IntentMergeBody) -> dict[str, Any]:
            intents = _intents(app)
            sources = [_get_intent(intents, i) for i in req.ids]
            try:
                merged = intents.merge_intents(
                    sources, statement=req.statement, intent_type=req.intent_type, scope=req.scope
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return merged.to_dict()

        @app.get("/intents")
        async def intent_query(
            agent: str | None = None,
            trust: str | None = None,
            status: str | None = None,
            scope: str | None = None,
            domain: str | None = None,
            rank: bool = False,
        ) -> dict[str, Any]:
            intents = _intents(app)
            ident = _agent(agent, trust)
            try:
                st = IntentStatus(status) if status else None
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"unknown status {status!r}") from exc
            if rank:  # Current-Engine force ranking over the readable set.
                found = intents.prioritize_intents(agent=ident, status=st)
            else:
                found = intents.query_intent(agent=ident, domain=domain, scope=scope, status=st)
            return {"intents": [i.to_dict() for i in found]}

        @app.get("/intents/{intent_id}")
        async def intent_get(intent_id: str) -> dict[str, Any]:
            return _get_intent(_intents(app), intent_id).to_dict()

        @app.post("/intents/{intent_id}/activate")
        async def intent_activate(intent_id: str, req: IntentGateBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            agent = _agent(req.agent, req.trust)
            context = AccessContext(consent_granted=req.consent)
            return intents.activate_intent(intent, agent=agent, context=context).to_dict()

        @app.post("/intents/{intent_id}/defer")
        async def intent_defer(intent_id: str, req: IntentGateBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            return intents.defer_intent(intent, reason=req.reason).to_dict()

        @app.post("/intents/{intent_id}/suppress")
        async def intent_suppress(intent_id: str, req: IntentGateBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            return intents.suppress_intent(intent, reason=req.reason).to_dict()

        @app.post("/intents/{intent_id}/conflicts")
        async def intent_conflicts(intent_id: str, req: IntentConflictBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            agent = _agent(req.agent, req.trust)
            conflict = intents.detect_conflicts(
                intent,
                agent=agent,
                threshold=req.threshold,
                tension=req.tension,
                recommended_resolution=req.recommended_resolution,
            )
            return {"conflict": _conflict_to_dict(conflict), "intent": intent.to_dict()}

        @app.post("/intents/{intent_id}/resolve")
        async def intent_resolve(intent_id: str, req: IntentResolveBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            agent = _agent(req.agent, req.trust)
            context = AccessContext(consent_granted=req.consent)
            return intents.resolve_conflict(intent, agent=agent, keep=req.keep, context=context).to_dict()

        @app.post("/intents/{intent_id}/split")
        async def intent_split(intent_id: str, req: IntentSplitBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            try:
                subs = intents.split_intent(intent, req.parts, scope=req.scope)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {"sub_intents": [s.to_dict() for s in subs], "parent": intent.to_dict()}

        @app.post("/intents/{intent_id}/handoff")
        async def intent_handoff(intent_id: str, req: IntentHandoffBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            agent = _agent(req.agent, req.trust)
            context = AccessContext(consent_granted=req.consent)
            if req.to == "plan":
                return intents.handoff_to_plan(intent, agent=agent, context=context).to_dict()
            return intents.handoff_to_judgment(intent, agent=agent, context=context).to_dict()

        @app.post("/intents/{intent_id}/retire")
        async def intent_retire(intent_id: str, req: IntentRetireBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            return intents.retire_intent(intent, outcome=req.outcome, reason=req.reason).to_dict()

        @app.post("/intents/{intent_id}/delete")
        async def intent_delete(intent_id: str, req: IntentGateBody) -> dict[str, Any]:
            intents = _intents(app)
            intent = _get_intent(intents, intent_id)
            # DELETE acts on the user's behalf (the user owns intent, PRD §17).
            agent = _agent(req.agent, req.trust, is_user_proxy=True)
            response: ProtocolResponse = intents.delete_intent(intent, agent=agent, context=AccessContext())
            return response.to_dict()

    return app


# Module-level app for uvicorn (e.g. ``uvicorn hydromemory.server:app``).
app = create_app()


def main() -> None:
    """Run the server with uvicorn (the ``hydromem-server`` entry point)."""
    import os

    import uvicorn

    host = os.environ.get("HYDRO_HOST", "127.0.0.1")
    port = int(os.environ.get("HYDRO_PORT", "8077"))
    uvicorn.run("hydromemory.server:app", host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
