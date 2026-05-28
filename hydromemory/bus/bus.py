"""In-process publish/subscribe event bus (contract + v2 Phase B1 implementation).

The bus is **sync at its core** (``publish`` is a plain ``def``) so the existing
synchronous verbs/pipeline/tests can emit without an event loop; WebSocket
subscribers are fed via bounded async queues drained on the server's event loop.
Delivery is permission-gated (``check_access``) — a subscriber never receives an
event about a droplet it cannot access.

This module ships the frozen contract: :class:`MemoryEvent` (see
:mod:`hydromemory.bus.events`), :class:`Subscription`, the :class:`EventBus`
interface, and a working no-op :data:`NULL_BUS`. Phase B1 (here) implements the
real fan-out, predicate/permission filtering, error isolation, and a re-entrancy
(cascade) guard.

Design (the real :class:`EventBus`):

* **Sync core.** ``publish`` iterates a *snapshot* of the active subscriptions so
  handlers may (un)subscribe during dispatch without mutating the loop. It
  returns the number of subscribers actually delivered to.
* **Matching.** A subscription receives the event when its topic set is ``None``
  (all topics) or contains ``event.type`` AND its ``predicate`` (if any) returns
  truthy. A raising predicate is treated as "no match" (and isolated).
* **Permission gate (fail-closed).** When the event names a ``droplet_id`` and
  the bus can load it, ``check_access(droplet, identity, AccessContext(),
  Operation.READ)`` decides delivery per-subscriber. When the event names a
  ``droplet_id`` that **cannot be gated** — the bus has no ``repo``, or the load
  failed/returned nothing — delivery is *denied* to any subscriber that carries
  an identity (we cannot prove the READ, so we do not leak the droplet's
  existence/metadata). An event with **no** ``droplet_id`` is topic-only and is
  delivered without a gate. A subscriber with no identity always gets topic-only
  delivery; an app-id string is coerced to ``AgentIdentity(name=app_id,
  trust_level=SESSION)``.
* **Error isolation.** A handler (or predicate, or the permission load) raising
  never stops the fan-out to the remaining subscribers.
* **Cascade guard.** Dispatch depth is tracked; a nested ``publish`` at or
  beyond ``max_depth`` (default 1) is dropped to prevent event storms. With the
  default ``max_depth=1`` a top-level publish is delivered but any publish it
  triggers from within a handler is dropped.
* **Handler kinds.** A plain sync callable is invoked inline. An ``asyncio.Queue``
  (duck-typed via ``put_nowait``) is fed with ``put_nowait``; on a full queue the
  oldest item is dropped so ``publish`` never blocks — this is the seam the B2
  WebSocket bridge drains.
"""
from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hydromemory.bus.events import MemoryEvent


@dataclass
class Subscription:
    id: str
    topics: frozenset[str] | None
    predicate: Callable[[MemoryEvent], bool] | None
    handler: Any  # a sync callable or an asyncio.Queue (server WS path)
    subscriber: Any = None  # AgentIdentity | AppIdentity used for permission gating
    active: bool = True


def _default_check_access() -> Callable[..., Any]:
    """Lazily resolve the governance ``check_access`` (avoids an import cycle)."""
    from hydromemory.governance import check_access

    return check_access


def _coerce_identity(subscriber: Any) -> Any | None:
    """Resolve a subscriber to something with a ``trust_level`` for gating.

    * ``None`` -> ``None`` (topic-only delivery; the permission gate is skipped).
    * An object that already looks like an identity (has ``trust_level``) is
      returned as-is.
    * An object exposing an ``.identity()`` method (e.g. a ``BaseAgent``) is asked
      for its :class:`AgentIdentity`.
    * Anything else (e.g. a bare app-id string) is coerced to
      ``AgentIdentity(name=str(subscriber), trust_level=SESSION)``.
    """
    if subscriber is None:
        return None
    if hasattr(subscriber, "trust_level"):
        return subscriber
    identity_fn = getattr(subscriber, "identity", None)
    if callable(identity_fn):
        try:
            ident = identity_fn()
        except Exception:  # noqa: BLE001 - never let identity resolution break publish
            ident = None
        if ident is not None and hasattr(ident, "trust_level"):
            return ident
    # Bare app-id (or anything else): coerce to a SESSION-trust agent identity.
    from hydromemory.governance import AgentIdentity, TrustLevel

    return AgentIdentity(name=str(subscriber), trust_level=TrustLevel.SESSION)


def _is_queue(handler: Any) -> bool:
    """Duck-type an ``asyncio.Queue`` by its non-blocking ``put_nowait`` API."""
    return callable(getattr(handler, "put_nowait", None))


class EventBus:
    """Publish/subscribe event bus (sync core, permission-gated, cascade-guarded)."""

    def __init__(
        self,
        *,
        repo: Any = None,
        check_access: Callable[..., Any] | None = None,
        max_depth: int = 1,
    ) -> None:
        self._repo = repo
        self._check_access = check_access if check_access is not None else _default_check_access()
        self._max_depth = max_depth
        self._subs: list[Subscription] = []
        self._ids = itertools.count(1)
        self._depth = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Publish
    # ------------------------------------------------------------------ #
    def publish(self, event: MemoryEvent) -> int:
        """Deliver ``event`` to matching, permitted subscribers; return the count.

        Synchronous: sync-callable handlers run inline; ``asyncio.Queue`` handlers
        get a non-blocking ``put_nowait`` (drop-oldest on a full queue). Handler
        and predicate errors are isolated; nested publishes beyond ``max_depth``
        are dropped (cascade guard).
        """
        # --- Cascade / re-entrancy guard. ------------------------------------
        # ``>=`` (not ``>``): a publish at or beyond ``max_depth`` is dropped, so
        # ``max_depth`` counts levels delivered (default 1 = top-level only).
        if self._depth >= self._max_depth:
            return 0

        # Snapshot active subscriptions so (un)subscribes during dispatch are safe.
        with self._lock:
            subs = [s for s in self._subs if s.active]

        # Resolve the droplet once (shared across subscribers) for the perm gate.
        droplet = self._load_droplet(event)

        delivered = 0
        self._depth += 1
        try:
            for sub in subs:
                if not self._topic_matches(sub, event):
                    continue
                if not self._predicate_ok(sub, event):
                    continue
                if not self._permitted(sub, event, droplet):
                    continue
                if self._deliver(sub, event):
                    delivered += 1
        finally:
            self._depth -= 1
        return delivered

    async def apublish(self, event: MemoryEvent) -> int:
        """Async convenience: run the sync :meth:`publish` (it never blocks)."""
        return self.publish(event)

    # ------------------------------------------------------------------ #
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------ #
    def subscribe(
        self,
        *,
        topics: frozenset[str] | None = None,
        predicate: Callable[[MemoryEvent], bool] | None = None,
        handler: Any,
        subscriber: Any = None,
    ) -> Subscription:
        sub = Subscription(
            id=f"sub_{next(self._ids)}",
            topics=topics,
            predicate=predicate,
            handler=handler,
            subscriber=subscriber,
            active=True,
        )
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            sub.active = False
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _topic_matches(sub: Subscription, event: MemoryEvent) -> bool:
        return sub.topics is None or event.type in sub.topics

    @staticmethod
    def _predicate_ok(sub: Subscription, event: MemoryEvent) -> bool:
        if sub.predicate is None:
            return True
        try:
            return bool(sub.predicate(event))
        except Exception:  # noqa: BLE001 - a bad predicate must not break the bus.
            return False

    def _load_droplet(self, event: MemoryEvent) -> Any | None:
        """Load the event's droplet (best-effort) for permission gating.

        Returns the droplet, or ``None`` when there is nothing to gate against —
        either the event has no ``droplet_id``, the bus has no repo, or the load
        failed/returned nothing. The caller (:meth:`_permitted`) distinguishes
        "no ``droplet_id``" (topic-only, allow) from "``droplet_id`` set but
        ungateable" (fail-closed) using ``event.droplet_id`` directly.
        """
        if event.droplet_id is None or self._repo is None:
            return None
        try:
            return self._repo.get(event.droplet_id)
        except Exception:  # noqa: BLE001 - repo failure must not break the bus.
            return None

    def _permitted(self, sub: Subscription, event: MemoryEvent, droplet: Any | None) -> bool:
        """Whether ``sub`` may receive ``event`` under the permission gate.

        Fail-closed. The decision turns on whether the event names a droplet and
        whether that droplet can be gated:

        * **No ``droplet_id``** — a topic-only event; delivered without a gate.
        * **``droplet_id`` set but ungateable** (no repo / load failed / not
          found) — delivered only to *anonymous* (identity-less) subscribers; an
          identified subscriber is DENIED, since we cannot prove its READ and must
          not leak the droplet's existence or metadata.
        * **``droplet_id`` set and loaded** — anonymous subscribers get delivery;
          an identified subscriber is decided by ``check_access(..., READ)``.
        """
        # Topic-only event: nothing to gate, deliver to everyone that matched.
        if event.droplet_id is None:
            return True

        identity = _coerce_identity(sub.subscriber)
        if identity is None:
            return True  # anonymous subscriber: topic-only delivery (no gate).

        # The event references a droplet but we could not load it to gate on:
        # fail closed for an identified subscriber rather than leak it.
        if droplet is None:
            return False

        from hydromemory.governance import AccessContext, Operation

        try:
            decision = self._check_access(droplet, identity, AccessContext(), Operation.READ)
        except Exception:  # noqa: BLE001 - a failing gate denies (fail-closed) but isolates.
            return False
        # L5: a non-AccessDecision (e.g. an injected check_access returning a bare
        # value) has no ``.allowed`` -> treat the missing attribute as DENY.
        allowed = getattr(decision, "allowed", None)
        if allowed is None:
            return False
        return bool(allowed)

    def _deliver(self, sub: Subscription, event: MemoryEvent) -> bool:
        """Deliver to one subscriber; return True on success. Errors are isolated."""
        handler = sub.handler
        try:
            if _is_queue(handler):
                self._enqueue(handler, event)
            else:
                handler(event)
            return True
        except Exception:  # noqa: BLE001 - one bad handler must not stop the fan-out.
            return False

    @staticmethod
    def _enqueue(queue: Any, event: MemoryEvent) -> None:
        """``put_nowait`` with drop-oldest on a full queue so publish never blocks."""
        try:
            queue.put_nowait(event)
        except Exception:  # noqa: BLE001 - asyncio.QueueFull (and any look-alike).
            # Drop the oldest item to make room, then enqueue the new event.
            try:
                queue.get_nowait()
            except Exception:  # noqa: BLE001 - empty/odd queue: nothing to drop.
                pass
            queue.put_nowait(event)


class NullEventBus(EventBus):
    """A bus that drops everything — the default so v1 stays event-free."""

    def __init__(self) -> None:
        # No repo/check_access needed; it never delivers.
        super().__init__(repo=None, check_access=lambda *a, **k: None)

    def publish(self, event: MemoryEvent) -> int:
        return 0

    def subscribe(
        self,
        *,
        topics: frozenset[str] | None = None,
        predicate: Callable[[MemoryEvent], bool] | None = None,
        handler: Any = None,
        subscriber: Any = None,
    ) -> Subscription:
        return Subscription(id="null", topics=topics, predicate=predicate, handler=handler, subscriber=subscriber, active=False)

    def unsubscribe(self, sub: Subscription) -> None:
        return None


#: Shared no-op bus instance (used by ``NULL_EMITTER`` and as the default).
NULL_BUS: EventBus = NullEventBus()
