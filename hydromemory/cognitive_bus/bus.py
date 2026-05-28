"""Unified HydroCognitive event bus (Master Spec §17).

§17 mandates *one* bus that routes cognitive objects across **all** stack layers —
memory, intent, judgment, plan, action, reflection, reintegration — "by type,
layer, permission, and owner". This is the cross-layer counterpart to the
droplet-centric memory bus (:mod:`hydromemory.bus.bus`): same sync fan-out and
fail-closed philosophy, but routing is by :class:`~hydromemory.canonical.envelope.ObjectType`
and gating is on the **canonical §8 envelope** rather than on a loaded droplet.

Why envelope-based gating? The memory bus gates by loading the event's droplet
through a repo and running ``check_access``. That is droplet-only — it cannot
decide an intent, plan, or reflection, and a single repo cannot resolve every
layer's store. Every cognitive object already projects to a
:class:`~hydromemory.canonical.envelope.CanonicalObject` carrying ``owner`` +
``permissions`` (§8), so the unified bus reads the decision straight off the
envelope: no repo, no per-layer load, works for all nine object types.

Design (mirrors :class:`~hydromemory.bus.bus.EventBus`):

* **Sync core.** ``publish`` iterates a snapshot of the active subscriptions so a
  handler may (un)subscribe during dispatch; it returns the count delivered.
* **Routing.** A subscription matches when its ``object_types`` is ``None`` (all
  types) or contains ``event.object_type`` AND its ``predicate`` (if any) returns
  truthy. A raising predicate is treated as "no match" and isolated.
* **Permission gate (fail-closed).** :func:`envelope_allows` decides delivery from
  the envelope's ``owner`` / ``permissions`` and the subscriber identity string —
  see that function for the exact rules. The default is DENY.
* **Error isolation.** A raising handler or predicate never stops the fan-out to
  the remaining subscribers.

The module imports only :mod:`hydromemory.canonical` and stdlib — never a layer
schema (``hydromemory.hydro*``). Publishers project layer objects to a
:class:`CanonicalObject` (via :mod:`hydromemory.canonical.projection`) *before*
publishing; the bus only ever sees the envelope.
"""
from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass

from hydromemory.canonical.envelope import CanonicalObject, ObjectType
from hydromemory.cognitive_bus.events import CognitiveEvent

CognitiveHandler = Callable[[CognitiveEvent], None]


def envelope_allows(obj: CanonicalObject, subscriber: str | None) -> bool:
    """Whether ``subscriber`` may receive an event about ``obj`` (fail-closed).

    The decision reads only the §8 envelope (``owner`` + ``permissions``) and the
    subscriber identity string — no repo, no droplet load — so it is uniform across
    every object type. Rules, in order:

    * ``subscriber is None`` (anonymous) → allowed **only** if visibility is
      ``"public"``; anonymous + non-public is DENIED (we do not leak a private or
      shared object to an unauthenticated listener).
    * ``subscriber == obj.owner`` → allowed (the owner always sees its own object).
    * ``subscriber in obj.permissions.allowed_agents`` → allowed (explicit grant).
    * visibility ``"public"`` → allowed for everyone.
    * otherwise → DENIED.

    Note ``"shared"`` is *not* a broadcast: a shared object reaches only its owner
    and the agents on its allow-list. Only ``"public"`` broadcasts.
    """
    perms = obj.permissions
    if subscriber is None:
        return perms.visibility == "public"
    if subscriber == obj.owner:
        return True
    if subscriber in perms.allowed_agents:
        return True
    if perms.visibility == "public":
        return True
    return False


@dataclass
class CognitiveSubscription:
    """A registered cross-layer subscriber.

    A minimal local type rather than :class:`hydromemory.bus.bus.Subscription`:
    routing is by ``object_types`` (a set of :class:`ObjectType`) not string topics,
    and ``subscriber`` is a plain identity *string* gated against the envelope (not
    an ``AgentIdentity`` run through ``check_access``). See ADR-0049.
    """

    id: str
    object_types: frozenset[ObjectType] | None
    subscriber: str | None
    predicate: Callable[[CognitiveEvent], bool] | None
    handler: CognitiveHandler
    active: bool = True


class CognitiveBus:
    """Unified publish/subscribe bus for canonical cognitive objects (§17)."""

    def __init__(self) -> None:
        self._subs: list[CognitiveSubscription] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------ #
    def subscribe(
        self,
        *,
        object_types: set[ObjectType] | None = None,
        subscriber: str | None = None,
        predicate: Callable[[CognitiveEvent], bool] | None = None,
        handler: CognitiveHandler,
    ) -> CognitiveSubscription:
        """Register ``handler`` for the given object types (``None`` = all types).

        ``subscriber`` is the agent/identity string used for the envelope permission
        gate (``None`` = an anonymous subscriber, which receives only public
        objects). ``predicate`` is an optional extra filter on the event.
        """
        sub = CognitiveSubscription(
            id=f"csub_{next(self._ids)}",
            object_types=frozenset(object_types) if object_types is not None else None,
            subscriber=subscriber,
            predicate=predicate,
            handler=handler,
            active=True,
        )
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: CognitiveSubscription) -> None:
        with self._lock:
            sub.active = False
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    # ------------------------------------------------------------------ #
    # Publish
    # ------------------------------------------------------------------ #
    def publish(self, event: CognitiveEvent) -> int:
        """Deliver ``event`` to every matching, permitted subscriber; return count.

        A subscription receives the event when (1) its ``object_types`` matches the
        envelope's type, (2) :func:`envelope_allows` permits its ``subscriber``, and
        (3) its ``predicate`` (if any) returns truthy. Synchronous; handler and
        predicate errors are isolated so one failure never stops the fan-out.
        """
        with self._lock:
            subs = [s for s in self._subs if s.active]

        delivered = 0
        for sub in subs:
            if not self._type_matches(sub, event):
                continue
            if not envelope_allows(event.object_ref, sub.subscriber):
                continue
            if not self._predicate_ok(sub, event):
                continue
            if self._deliver(sub, event):
                delivered += 1
        return delivered

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _type_matches(sub: CognitiveSubscription, event: CognitiveEvent) -> bool:
        return sub.object_types is None or event.object_type in sub.object_types

    @staticmethod
    def _predicate_ok(sub: CognitiveSubscription, event: CognitiveEvent) -> bool:
        if sub.predicate is None:
            return True
        try:
            return bool(sub.predicate(event))
        except Exception:  # noqa: BLE001 - a bad predicate must not break the bus.
            return False

    @staticmethod
    def _deliver(sub: CognitiveSubscription, event: CognitiveEvent) -> bool:
        """Deliver to one subscriber; return True on success. Errors are isolated."""
        try:
            sub.handler(event)
            return True
        except Exception:  # noqa: BLE001 - one bad handler must not stop the fan-out.
            return False


class NullCognitiveBus(CognitiveBus):
    """A bus that drops everything — the default analog to ``NULL_BUS``/``NULL_EMITTER``."""

    def publish(self, event: CognitiveEvent) -> int:
        return 0

    def subscribe(
        self,
        *,
        object_types: set[ObjectType] | None = None,
        subscriber: str | None = None,
        predicate: Callable[[CognitiveEvent], bool] | None = None,
        handler: CognitiveHandler,
    ) -> CognitiveSubscription:
        return CognitiveSubscription(
            id="null",
            object_types=frozenset(object_types) if object_types is not None else None,
            subscriber=subscriber,
            predicate=predicate,
            handler=handler,
            active=False,
        )

    def unsubscribe(self, sub: CognitiveSubscription) -> None:
        return None


#: Shared no-op cognitive bus instance (the default until a real bus is wired in).
NULL_COGNITIVE_BUS: CognitiveBus = NullCognitiveBus()


__all__ = [
    "CognitiveHandler",
    "envelope_allows",
    "CognitiveSubscription",
    "CognitiveBus",
    "NullCognitiveBus",
    "NULL_COGNITIVE_BUS",
]
