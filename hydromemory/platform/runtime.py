"""Mesh engine-adapter + shared-backing wiring helpers (v2 Phase B2).

This module is the integration seam that wires the Phase B1 pieces — the
contamination functions, the §8 agent runtime, the encrypted/audited
:class:`~hydromemory.vault.vault.VaultRepository`, and the
:class:`~hydromemory.platform.mesh.Mesh` — into something that actually runs end
to end. It adds **no storage behavior of its own**: the adapter delegates to the
pure :mod:`hydromemory.contamination` functions (which mutate-and-return a
droplet) and lets the mesh own all persistence via the vault.

Two things live here:

* :class:`MeshEngine` — the narrow "engine" the mesh reactions and the
  filtration/reflection roles call. The mesh's :data:`DEFAULT_REACTIONS` invoke
  ``agent.engine.assess_and_route(droplet, {})`` (ABSORBED), ``.filter(droplet)``
  (POLLUTED) and ``.reverify(droplet)`` (DISTILLED); the ``FiltrationAgent``
  calls ``assess_and_route`` + ``filter`` and the ``ReflectionAgent`` calls
  ``aged_droplets`` + ``reverify``. :class:`MeshEngine` implements exactly that
  union over a single injected ``intelligence`` (its ``.detector``), so a
  ``build_default_runtime(MeshEngine(...))`` runtime is fully satisfied.

* :func:`build_mesh` — assemble a :class:`MeshEngine`, a default runtime, and a
  :class:`Mesh` (the caller calls ``.attach()``).

* :func:`build_app_views` — build per-app + owner :class:`VaultRepository` views
  over ONE shared backing (so the in-process vector index and the on-disk rows
  are shared across scopes, avoiding the multi-connection / stale-index problem
  of opening a fresh store per app). Used by the L1/L2 scenarios.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from hydromemory import contamination as contamination_mod

if TYPE_CHECKING:
    from hydromemory.bus.bus import EventBus
    from hydromemory.governance import AgentIdentity
    from hydromemory.intelligence import Intelligence
    from hydromemory.platform.mesh import Mesh
    from hydromemory.schema import Droplet
    from hydromemory.storage.sqlite_repository import SqliteDropletRepository
    from hydromemory.vault.audit import AuditLog
    from hydromemory.vault.cipher import Cipher
    from hydromemory.vault.vault import VaultRepository


class MeshEngine:
    """The narrow engine-adapter the mesh + filtration/reflection roles call.

    Delegates the contamination work to :mod:`hydromemory.contamination` (pure,
    mutate-and-return). It deliberately does **not** *write* storage — the mesh
    persists the proposed droplet through the vault, so this adapter only
    transforms droplets in memory. (For ADR-0032 it may hold an optional
    *read-only* ``repo`` used solely to *select* aged droplets; it still never
    writes.)

    Each transform operates on a *copy* and returns that copy (never the input
    instance). This matters for the mesh: :meth:`Mesh._unchanged` treats a
    returned-same-instance (``before is after``) as a no-op and suppresses the
    upsert. The :mod:`hydromemory.contamination` helpers mutate-and-return the
    *same* object, so the adapter copies first — the mesh then sees a distinct
    object, compares ``to_dict``, and correctly detects the real change.

    Methods (the union the §8 roles driven by the mesh actually use):

    * ``assess_and_route(droplet, context)`` — run contamination detection and
      route a flagged droplet to the contaminated pool (delegates to
      :func:`contamination.assess_and_route` with this engine's detector).
    * ``filter(droplet)`` — repair a polluted droplet into a filtered one
      (delegates to :func:`contamination.filter_droplet`).
    * ``reverify(droplet)`` — re-assess the droplet against the detector and
      stamp ``state.confidence`` + ``cycle.last_verified``; returns the (copied)
      droplet WITHOUT persisting (the mesh owns the upsert). If the detector now
      flags it, it is routed to the contaminated pool.
    * ``aged_droplets(context)`` — the reflection role's candidate fetch:
      explicit ``context['droplets']``, else a real ``select_aged`` query when a
      read-only ``repo`` is wired (ADR-0032), else an empty passthrough.
    * ``decay(droplet, idle_cycles=...)`` — passively fade a droplet's salience
      (``forgetting.decay``; salience-only, never purity/integrity/confidence).
    * ``cluster(droplets, context)`` — group droplets into constellations
      (delegates to :func:`hydromemory.activation.cluster`); the
      ``DistillationAgent``'s grouping surface (ADR-0031).
    * ``distill(cluster)`` — derive one CLOUD principle droplet from a cluster
      (storage-free; the runtime/mesh owns the upsert; ADR-0036).
    """

    def __init__(self, intelligence: Intelligence, *, repo: Any = None, decay_config: Any = None) -> None:
        self.intelligence = intelligence
        # Optional READ-ONLY repo for aged-droplet selection (ADR-0032). The
        # adapter still never persists — the mesh owns writes.
        self.repo = repo
        self.decay_config = decay_config

    @property
    def detector(self) -> Any:
        """The contamination detector the assessment methods run against."""
        return self.intelligence.detector

    @staticmethod
    def _copy(droplet: Droplet) -> Droplet:
        """A deep-ish copy of ``droplet`` (embedding included) as a NEW instance.

        Returning a distinct object is what lets the mesh detect a real change:
        its ``_unchanged`` short-circuits to True when ``before is after``.
        """
        from hydromemory.schema import Droplet as _Droplet

        return _Droplet.from_dict(droplet.to_dict(include_embedding=True))

    # -- filtration surface ---------------------------------------------------
    def assess_and_route(self, droplet: Droplet, context: dict[str, Any] | None = None) -> Droplet:
        """Assess + route via :func:`contamination.assess_and_route` (no persist).

        Operates on a copy so the returned droplet is a distinct instance (see
        :meth:`_copy`).
        """
        return contamination_mod.assess_and_route(
            self._copy(droplet), context or {}, self.intelligence.detector
        )

    def filter(self, droplet: Droplet) -> Droplet:
        """Filter a polluted droplet via :func:`contamination.filter_droplet`."""
        return contamination_mod.filter_droplet(self._copy(droplet), self.intelligence.detector)

    # -- reflection surface ---------------------------------------------------
    def reverify(self, droplet: Droplet) -> Droplet:
        """Re-check ``droplet`` against the detector; stamp confidence + verified.

        Mirrors the reflection role's "is this still accurate?" pass. We re-run
        the contamination detector on a copy: a still-clean droplet has its
        confidence set to the detector's confidence and ``cycle.last_verified``
        set; a now-contaminated droplet is routed to the contaminated pool. The
        (copied) droplet is returned but never persisted here — the mesh upserts
        it through the vault.
        """
        copy = self._copy(droplet)
        verdict = self.intelligence.detector.assess(copy, {})
        copy.cycle.last_verified = datetime.now(UTC)
        copy.meta["reverified"] = True
        if verdict.contaminated:
            contamination_mod.mark_polluted(copy, verdict.reason)
            copy.meta["contamination_confidence"] = float(verdict.confidence)
        else:
            # Re-verified clean: record the verification confidence so the
            # proposal is an observable change (the mesh suppresses no-ops).
            copy.state.confidence = float(verdict.confidence)
        return copy

    def aged_droplets(self, context: dict[str, Any] | None = None) -> list[Droplet]:
        """Reflection's candidate fetch (ADR-0032).

        Explicit ``context['droplets']`` win (back-compat); else, when a
        read-only ``repo`` is wired, a real store-backed selection of
        under-verified / never-verified droplets (``forgetting.select_aged``);
        else (no repo) the empty passthrough, unchanged.
        """
        context = context or {}
        droplets = context.get("droplets")
        if droplets:
            return list(droplets)
        if self.repo is not None:
            from hydromemory import forgetting as _forgetting

            return _forgetting.select_aged(self.repo)
        return []

    def decay(self, droplet: Droplet, *, idle_cycles: int = 1) -> Droplet:
        """Passively fade a droplet's salience (ADR-0032); operates on a copy.

        Delegates to :func:`hydromemory.forgetting.decay` — salience-only
        (pressure/fluidity/temperature), never purity/integrity/confidence — and
        returns a distinct instance so the mesh's no-op guard detects the change.
        """
        from hydromemory import forgetting as _forgetting

        return _forgetting.decay(
            self._copy(droplet),
            idle_cycles=idle_cycles,
            config=self.decay_config or _forgetting.DEFAULT_DECAY,
        )

    # -- consolidation surface (ADR-0031) -------------------------------------
    def cluster(self, droplets: list[Droplet], context: dict[str, Any] | None = None) -> list[list[Droplet]]:
        """Group ``droplets`` into constellations for distillation.

        Delegates to :func:`hydromemory.activation.cluster` — connected components
        over the association/support/derived_from link subgraph — so the
        ``DistillationAgent``'s ``engine.cluster`` surface is real (it previously
        had no implementation). Storage-free: the graph is read from each droplet's
        own ``links``, matching this adapter's mutate-in-memory contract.
        """
        from hydromemory.activation import LINK_KINDS
        from hydromemory.activation import cluster as _cluster

        members = list(droplets)
        by_id = {d.id: d for d in members}

        def neighbors(droplet_id: str) -> list[tuple[str, str]]:
            d = by_id.get(droplet_id)
            if d is None:
                return []
            return [(dst, kind) for kind in LINK_KINDS for dst in getattr(d.links, kind)]

        return _cluster(members, neighbors)

    def distill(self, cluster: list[Droplet]) -> Droplet:
        """Derive one high-purity principle droplet from a cluster (no persist).

        Mirrors ``Verbs.distill`` but storage-free — the mesh/runtime owns the
        upsert. Lands a GROUNDWATER-phase principle in the CLOUD reservoir (the
        abstraction layer) with ``derived_from`` provenance and abstractor-derived
        text, so a distilled principle becomes reusable structured reasoning that
        ordinary approved agents can recall (ADR-0036), and ``abstraction_bonus``
        (phase-keyed) can let it outrank its sources. SACRED is reserved for
        user-declared anchors, not system-distilled reasoning. Empty cluster ->
        ``ValueError``.
        """
        from hydromemory.reservoirs import Reservoir
        from hydromemory.schema import Droplet as _Droplet
        from hydromemory.schema import Phase, State, clamp_unit, new_id

        members = list(cluster)
        if not members:
            raise ValueError("distill requires a non-empty cluster")
        joined = "; ".join(d.content for d in members)
        text = self.intelligence.abstractor.evaporate(joined)
        principle = _Droplet(
            id=new_id(),
            content=text,
            source="distill",
            phase=Phase.GROUNDWATER,
            reservoir=Reservoir.CLOUD,
            state=State(
                purity=clamp_unit(max(d.state.purity for d in members) + 0.05),
                gravity=clamp_unit(max(d.state.gravity for d in members) + 0.1),
                integrity=clamp_unit(max(d.state.integrity for d in members)),
                confidence=clamp_unit(sum(d.state.confidence for d in members) / len(members)),
                depth=0.6,
            ),
            embedding=self.intelligence.embedder.embed(text),
        )
        principle.meta["principle"] = text
        principle.meta["distilled_from"] = [d.id for d in members]
        for d in members:
            principle.links.derived_from.append(d.id)
        return principle


def build_mesh(
    vault: VaultRepository,
    bus: EventBus,
    intelligence: Intelligence,
    audit: AuditLog | None = None,
    *,
    max_depth: int = 1,
    consolidate: bool = False,
    consolidation_min_size: int = 2,
) -> Mesh:
    """Wire a :class:`Mesh` over ``vault`` + ``bus`` with a :class:`MeshEngine`.

    Builds a :class:`MeshEngine` from ``intelligence``, a default §8 runtime
    bound to it (so the filtration + reflection roles the mesh drives are
    registered), and returns the assembled :class:`Mesh`. The caller is
    responsible for calling :meth:`Mesh.attach` to subscribe the reactions.

    ``consolidate=True`` additionally enables the opt-in autonomic consolidation
    reaction (ADR-0031): the distillation role gather-then-distills dense
    constellations into SACRED principles. Default-off keeps the standard mesh
    behavior unchanged (ADR-0025).
    """
    from hydromemory.agents.registry import build_default_runtime
    from hydromemory.platform.mesh import Mesh

    mesh_engine = MeshEngine(intelligence, repo=vault)
    runtime = build_default_runtime(mesh_engine)
    return Mesh(
        runtime,
        bus,
        vault,
        audit,
        max_depth=max_depth,
        consolidate=consolidate,
        consolidation_min_size=consolidation_min_size,
    )


def build_app_views(
    backing: SqliteDropletRepository,
    cipher: Cipher,
    audit: AuditLog,
    *,
    app_ids: list[str],
    identity: AgentIdentity | None = None,
    owner_identity: AgentIdentity | None = None,
) -> tuple[dict[str, VaultRepository], VaultRepository]:
    """Build per-app + an owner :class:`VaultRepository` view over ONE backing.

    All views share the *same* ``backing`` (one :class:`SqliteDropletRepository`,
    hence one SQLite connection and one in-process vector index), so writes
    through any app view are immediately visible to the owner view and the
    vector index never goes stale across scopes — the multi-connection trap the
    "open a fresh store per app" path would hit.

    Returns ``(app_views, owner_view)`` where:

    * ``app_views[app_id]`` is an L1 :class:`VaultRepository` scoped to
      ``AppScope(app_id=app_id)`` under ``identity`` (default: a user-proxy);
    * ``owner_view`` is an L2 cross-app :class:`VaultRepository`
      (``AppScope(cross_app=True)``) under ``owner_identity`` (default:
      user-proxy) that aggregates every app's droplets.
    """
    from hydromemory.governance import AgentIdentity, TrustLevel
    from hydromemory.vault.scope import AppScope
    from hydromemory.vault.vault import VaultRepository

    def _proxy() -> AgentIdentity:
        return AgentIdentity(name="user", trust_level=TrustLevel.HIGH_TRUST, is_user_proxy=True)

    app_identity = identity or _proxy()
    owner = owner_identity or _proxy()

    app_views: dict[str, VaultRepository] = {}
    for app_id in app_ids:
        app_views[app_id] = VaultRepository(
            backing,
            cipher,
            audit,
            identity=app_identity,
            scope=AppScope(app_id=app_id),
        )
    owner_view = VaultRepository(
        backing,
        cipher,
        audit,
        identity=owner,
        scope=AppScope(cross_app=True),
    )
    return app_views, owner_view
