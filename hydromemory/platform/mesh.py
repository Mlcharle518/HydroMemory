"""L3 Agentic Memory Mesh — agents coordinate via the shared bus (contract).

The mesh wraps the existing §8 roles (and external agents) as bus subscribers
that react to :class:`MemoryEvent`s and propose vault operations, each
permission-checked and de-conflicted, with bounded cascade depth so an event
cannot storm. It builds on the bus + the synchronous ``AgentRuntime`` WITHOUT
modifying ``tick`` (v1's path stays intact). Phase B1 implements the reactions.

Reaction table (EventType -> reaction):
    ABSORBED  -> filtration assesses + routes a freshly absorbed droplet
                 (``assess_and_route``), operation MUTATE.
    POLLUTED  -> filtration filters a flagged-polluted droplet into a usable,
                 filtered droplet (``filter``), operation TRANSFORM.
    DISTILLED -> reflection re-verifies an aged/derived droplet (``reverify``),
                 operation MUTATE.

Each reaction: load the droplet via the vault, ``check_access`` for the
reaction's operation under the agent's identity (SKIP + audit on deny), apply
the agent's proposed op, and — if the proposal actually changed the droplet —
upsert it and emit a derived follow-on event carrying ``_depth + 1``.

Cascade safety:
    * depth guard — a reaction only fires while ``payload["_depth"] < max_depth``;
      every emitted follow-on carries ``_depth + 1``.
    * per-cycle dedupe — the tuple ``(event_type, droplet_id, agent_name)`` fires
      at most once for the lifetime of this mesh's subscriptions.
    * no-op suppression — if the agent's proposed droplet is unchanged, nothing
      is upserted and no follow-on is emitted.
    * terminal phases — FILTERED / ARCHIVED droplets are never re-reacted to.

Opt-in consolidation (ADR-0031): with ``consolidate=True`` the mesh also runs a
many->one *gather-then-distill* reaction the single-droplet ``Reaction`` table
cannot express — on the trigger event (default ABSORBED) it gathers the seed's
linked constellation, density-gates on size, and ``cluster``->``distill``s it into
a CLOUD principle (ADR-0036; reusable by ordinary approved agents at recall),
written through a user-proxy view and announced as DISTILLED.
Default-off, and bounded by the same depth guard + per-cycle dedupe.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hydromemory.bus.bus import EventBus, Subscription
from hydromemory.bus.events import EventType, MemoryEvent
from hydromemory.governance import AccessContext, AgentIdentity, Operation, TrustLevel, check_access
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Phase

# Phases that are terminal for the mesh: a droplet in one of these is a finished
# product (already filtered, or archived out) and must not trigger re-reaction.
_TERMINAL_PHASES: frozenset[Phase] = frozenset({Phase.FILTERED})
_TERMINAL_EVENTS: frozenset[str] = frozenset(
    {EventType.FILTERED.value, EventType.ARCHIVED.value}
)


@dataclass(frozen=True)
class Reaction:
    """One mesh reaction: an event topic bound to a role + the op it proposes.

    ``trigger``   — the EventType this reaction subscribes to.
    ``agent_role``— the §8 role name on the runtime that performs the work.
    ``operation`` — the governance Operation the proposal is access-checked under.
    ``apply``     — given (agent, droplet), returns the proposed (possibly new)
                    droplet by calling the agent's narrow engine surface.
    ``emits``     — the EventType emitted for a non-no-op proposal (cascade).
    """

    trigger: EventType
    agent_role: str
    operation: Operation
    apply: Callable[[Any, Any], Any]
    emits: EventType


def _apply_assess_and_route(agent: Any, droplet: Any) -> Any:
    return agent.engine.assess_and_route(droplet, {})


def _apply_filter(agent: Any, droplet: Any) -> Any:
    return agent.engine.filter(droplet)


def _apply_reverify(agent: Any, droplet: Any) -> Any:
    return agent.engine.reverify(droplet)


# The default reaction table (see module docstring).
DEFAULT_REACTIONS: tuple[Reaction, ...] = (
    Reaction(EventType.ABSORBED, "filtration", Operation.MUTATE, _apply_assess_and_route, EventType.TRANSFORMED),
    Reaction(EventType.POLLUTED, "filtration", Operation.TRANSFORM, _apply_filter, EventType.FILTERED),
    Reaction(EventType.DISTILLED, "reflection", Operation.MUTATE, _apply_reverify, EventType.TRANSFORMED),
)


class Mesh:
    def __init__(
        self,
        runtime: Any,  # AgentRuntime
        bus: EventBus,
        vault: Any,  # VaultRepository
        audit: Any = None,  # AuditLog
        *,
        max_depth: int = 1,
        consolidate: bool = False,
        consolidation_min_size: int = 2,
        consolidation_trigger: EventType = EventType.ABSORBED,
        consolidation_max_nodes: int = 32,
    ) -> None:
        self.runtime = runtime
        self.bus = bus
        self.vault = vault
        self.audit = audit
        self.max_depth = max_depth
        # Opt-in autonomic consolidation (ADR-0031): default-off so the standard
        # mesh behavior is unchanged (ADR-0025).
        self.consolidate = consolidate
        self.consolidation_min_size = consolidation_min_size
        self.consolidation_trigger = consolidation_trigger
        self.consolidation_max_nodes = consolidation_max_nodes
        self._reactions: list[Reaction] = list(DEFAULT_REACTIONS)
        # Per-cycle dedupe over (event_type, droplet_id, agent_name).
        self._fired: set[tuple[str, str | None, str]] = set()
        self._subscriptions: list[Subscription] = []
        # Lazily-built user-proxy view for principle writes (SACRED needs it).
        self._principal: Any = None

    # -- wiring ---------------------------------------------------------------

    def attach(self) -> list[Subscription]:
        """Subscribe every reaction to the bus; return the subscriptions."""
        self._subscriptions = []
        for reaction in self._reactions:
            agent = self._agent_for(reaction.agent_role)
            if agent is None:
                continue  # role not registered on this runtime; skip silently
            sub = self.bus.subscribe(
                topics=frozenset({reaction.trigger.value}),
                handler=self._make_handler(reaction, agent),
                subscriber=self._identity(agent),
            )
            self._subscriptions.append(sub)

        # Opt-in autonomic consolidation: a many->one "gather-then-distill"
        # reaction the single-droplet Reaction table cannot express (ADR-0031).
        if self.consolidate:
            distiller = self._agent_for("distillation")
            if distiller is not None:
                sub = self.bus.subscribe(
                    topics=frozenset({self.consolidation_trigger.value}),
                    handler=self._make_consolidation_handler(distiller),
                    subscriber=self._identity(distiller),
                )
                self._subscriptions.append(sub)
        return self._subscriptions

    def register_external(self, agent: Any, reactions: list[Reaction]) -> None:
        """Register an external agent + its reactions, then (re)attach them."""
        self.runtime.register(agent)
        for reaction in reactions:
            self._reactions.append(reaction)
            sub = self.bus.subscribe(
                topics=frozenset({reaction.trigger.value}),
                handler=self._make_handler(reaction, agent),
                subscriber=self._identity(agent),
            )
            self._subscriptions.append(sub)

    def reset_cycle(self) -> None:
        """Clear the per-cycle dedupe set (start of a fresh reaction cycle)."""
        self._fired.clear()

    # -- reaction core --------------------------------------------------------

    def _make_handler(self, reaction: Reaction, agent: Any) -> Callable[[MemoryEvent], None]:
        def handler(event: MemoryEvent) -> None:
            self._react(reaction, agent, event)

        return handler

    def _react(self, reaction: Reaction, agent: Any, event: MemoryEvent) -> None:
        # Terminal events never trigger further reaction.
        if event.type in _TERMINAL_EVENTS:
            return

        depth = int(event.payload.get("_depth", 0) or 0)
        if depth >= self.max_depth:
            return  # cascade depth guard

        droplet_id = event.droplet_id
        if droplet_id is None:
            return

        agent_name = self._identity(agent).name
        key = (event.type, droplet_id, agent_name)
        if key in self._fired:
            return  # per-cycle dedupe
        self._fired.add(key)

        droplet = self.vault.get(droplet_id)
        if droplet is None:
            return

        # Terminal-phase droplets are finished products; do not re-react.
        if getattr(droplet, "phase", None) in _TERMINAL_PHASES:
            return

        identity = self._identity(agent)
        decision = check_access(droplet, identity, AccessContext(), reaction.operation)
        if not decision.allowed:
            self._audit(identity, reaction, droplet, decision)
            return  # SKIP denied reactions (audited)

        proposed = reaction.apply(agent, droplet)
        if proposed is None or self._unchanged(droplet, proposed):
            return  # no-op proposal: emit nothing, upsert nothing

        self.vault.upsert(proposed)
        self.bus.publish(
            MemoryEvent(
                type=reaction.emits.value,
                actor=agent_name,
                droplet_id=getattr(proposed, "id", droplet_id),
                app_id=event.app_id,
                payload={"_depth": depth + 1},
            )
        )

    # -- consolidation (ADR-0031, many->one) ----------------------------------

    def _make_consolidation_handler(self, agent: Any) -> Callable[[MemoryEvent], None]:
        def handler(event: MemoryEvent) -> None:
            self._consolidate(agent, event)

        return handler

    def _consolidate(self, agent: Any, event: MemoryEvent) -> None:
        """Gather the event droplet's constellation and distill each cluster.

        Unlike the single-droplet reactions, this is many->one: it reads the
        seed's linked neighborhood (the activation/cluster graph), density-gates
        on size, then `cluster`->`distill`s into one principle per component,
        persisted through the principal view and announced as `DISTILLED`. (The
        principle lands in CLOUD per ADR-0036, so the user-proxy view is no longer
        *required* for the write — but it is retained as the principal/owner
        writer of consolidated memory.) Bounded by the
        same depth guard + per-cycle dedupe as `_react`; principles are never
        themselves re-consolidated.
        """
        if event.type in _TERMINAL_EVENTS:
            return
        depth = int(event.payload.get("_depth", 0) or 0)
        if depth >= self.max_depth:
            return
        droplet_id = event.droplet_id
        if droplet_id is None:
            return
        agent_name = self._identity(agent).name
        key = (event.type, droplet_id, agent_name)
        if key in self._fired:
            return
        self._fired.add(key)

        principal = self._principal_vault()
        seed = principal.get(droplet_id)
        if seed is None or self._is_principle(seed):
            return  # nothing to consolidate (or seed is itself a principle)

        members = self._gather_constellation(principal, seed)
        if len(members) < self.consolidation_min_size:
            return  # density gate: too sparse to be worth a principle

        identity = principal.identity
        for group in agent.engine.cluster(members, {}):
            if len(group) < self.consolidation_min_size:
                continue
            principle = agent.engine.distill(group)
            decision = check_access(principle, identity, AccessContext(), Operation.MUTATE)
            if not decision.allowed:
                if self.audit is not None:
                    self.audit.append(
                        actor=identity.name,
                        app_id=None,
                        operation=Operation.MUTATE.value,
                        droplet_id=getattr(principle, "id", None),
                        decision=decision,
                        detail="mesh consolidation denied",
                    )
                continue
            principal.upsert(principle)
            self.bus.publish(
                MemoryEvent(
                    type=EventType.DISTILLED.value,
                    actor=agent_name,
                    droplet_id=principle.id,
                    app_id=event.app_id,
                    payload={"_depth": depth + 1, "distilled_from": principle.meta.get("distilled_from", [])},
                )
            )

    def _gather_constellation(self, repo: Any, seed: Any) -> list[Any]:
        """BFS the seed's grouping-link neighborhood (bounded by max_nodes)."""
        seen = {seed.id}
        members = [seed]
        queue: deque[Any] = deque([seed])
        while queue and len(members) < self.consolidation_max_nodes:
            cur = queue.popleft()
            for kind in ("associations", "supports", "derived_from"):
                for nid in getattr(cur.links, kind, []) or []:
                    if nid in seen:
                        continue
                    seen.add(nid)
                    neighbor = repo.get(nid)
                    if neighbor is None or self._is_principle(neighbor):
                        continue  # missing, out-of-scope, or already a principle
                    members.append(neighbor)
                    queue.append(neighbor)
        return members

    def _principal_vault(self) -> Any:
        """A user-proxy view for principle writes (cached).

        Consolidated principles are the user/owner's distilled memory, so they are
        written through a principal (user-proxy) identity. Since ADR-0036 lands
        them in CLOUD (approved-agent reservoir) this is no longer *required* for
        the write to clear governance (it was when principles were SACRED), but it
        keeps the owner as the writer of consolidated memory. Derive a sibling
        :class:`VaultRepository` over the *same* backing/cipher/audit so the write
        shares one connection + vector index (the build_app_views pattern). If the
        vault is not a VaultRepository, fall back to it directly.
        """
        if self._principal is not None:
            return self._principal
        v = self.vault
        backing = getattr(v, "backing", None)
        cipher = getattr(v, "cipher", None)
        audit = getattr(v, "audit", None)
        if backing is None or cipher is None or audit is None:
            self._principal = v
            return self._principal
        from hydromemory.vault.scope import AppScope
        from hydromemory.vault.vault import VaultRepository

        self._principal = VaultRepository(
            backing,
            cipher,
            audit,
            identity=AgentIdentity(name="user", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True),
            scope=AppScope(cross_app=True),
        )
        return self._principal

    @staticmethod
    def _is_principle(droplet: Any) -> bool:
        """A distilled principle — never re-consolidate.

        Identified by ``source=="distill"`` / a ``principle`` meta marker (the
        robust signals after ADR-0036 moved principles to CLOUD); SACRED is still
        treated as a principle-bearing reservoir for back-compat with any
        previously-distilled SACRED principles.
        """
        return (
            getattr(droplet, "source", None) == "distill"
            or bool(getattr(droplet, "meta", {}).get("principle"))
            or getattr(droplet, "reservoir", None) is Reservoir.SACRED
        )

    # -- helpers --------------------------------------------------------------

    def _agent_for(self, role: str) -> Any | None:
        for agent in getattr(self.runtime, "agents", ()):  # tuple of registered agents
            if getattr(agent, "name", None) == role:
                return agent
        return None

    @staticmethod
    def _identity(agent: Any) -> AgentIdentity:
        ident = getattr(agent, "identity", None)
        if callable(ident):
            return ident()  # type: ignore[no-any-return]
        return AgentIdentity(name=getattr(agent, "name", "agent"))

    @staticmethod
    def _unchanged(before: Any, after: Any) -> bool:
        """True if the proposal left the droplet effectively unchanged.

        Compares the canonical ``to_dict`` when available (identity of object is
        not enough — agents may return the same instance after mutating it).
        """
        if before is after:
            # Same object returned: treat as unchanged only if it has no diff
            # signal. Without a snapshot we conservatively say unchanged.
            return True
        to_dict = getattr(before, "to_dict", None)
        if callable(to_dict) and hasattr(after, "to_dict"):
            return before.to_dict() == after.to_dict()
        return before == after

    def _audit(self, identity: AgentIdentity, reaction: Reaction, droplet: Any, decision: Any) -> None:
        if self.audit is None:
            return
        self.audit.append(
            actor=identity.name,
            app_id=None,
            operation=reaction.operation.value,
            droplet_id=getattr(droplet, "id", None),
            decision=decision,
            detail="mesh reaction denied",
        )
