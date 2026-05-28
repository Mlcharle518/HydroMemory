"""Query-conditioned spreading activation over the links graph.

This is the §4 "spine" of ``docs/closing-the-gaps.md`` and the realization of
``docs/research/memory-as-interacting-network.md``. v1 recall scores each droplet
*in isolation* (``hydro_recall_score``) and never traverses ``links``; this module
lets a question flow through the link graph so a *constellation* of related
droplets surfaces together (multi-hop recall), and provides the ``cluster``
primitive that autonomic consolidation needs (the ``engine.cluster`` surface the
DistillationAgent calls).

The flow is parameterized by the droplet's own hydraulic state vector — nothing new
is invented:

* ``fluidity``  -> conductance: how readily activation flows *out* of a node.
* ``depth``     -> resistance: how much a target damps incoming activation.
* ``purity``    -> mixing / epistemic hygiene: a low-purity node contributes *less*
  of its activation downstream, so contaminated memory cannot flood the
  constellation it flows into.

Pure + dependency-injected: callers pass ``neighbors`` and ``states`` accessors, so
this module never imports the repository and stays deterministic and offline.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from hydromemory.schema import Droplet, State

# The four canonical link kinds (mirror ``schema.Links`` fields).
LINK_KINDS: tuple[str, ...] = ("associations", "supports", "derived_from", "contradictions")

# Kinds used to *group* droplets for consolidation. Contradictions are excluded:
# memories that contradict each other must not be distilled into one principle.
GROUPING_KINDS: frozenset[str] = frozenset({"associations", "supports", "derived_from"})

# Default per-kind base conductance (how strongly each edge type carries flow).
_DEFAULT_EDGE_WEIGHTS: dict[str, float] = {
    "associations": 1.0,
    "supports": 0.9,
    "derived_from": 0.7,
    "contradictions": 0.6,
}

# Default intent -> preferred link kinds. When the query carries a recognized
# intent, only the preferred kinds conduct (the question selects the roots it
# flows through); an unknown/absent intent follows all kinds at their base weight.
_DEFAULT_INTENT_EDGES: dict[str, tuple[str, ...]] = {
    "exception": ("contradictions", "derived_from"),
    "exceptions": ("contradictions", "derived_from"),
    "contradiction": ("contradictions", "derived_from"),
    "evidence": ("supports", "associations"),
    "support": ("supports", "associations"),
    "why": ("supports", "associations"),
    "definition": ("associations", "supports", "derived_from"),
    "meaning": ("associations", "supports", "derived_from"),
    "related": ("associations", "supports", "derived_from"),
    "context": ("associations", "supports", "derived_from"),
    "currency": ("derived_from", "contradictions"),
    "supersede": ("derived_from", "contradictions"),
    "supersedes": ("derived_from", "contradictions"),
}

# Accessor types injected by the caller (the pipeline/Verbs build these over the repo).
NeighborsFn = Callable[[str], Sequence[tuple[str, str]]]  # id -> [(neighbor_id, link_kind), ...]
StatesFn = Callable[[str], "State | None"]  # id -> hydraulic State (None if unknown)


@dataclass(frozen=True)
class ActivationParams:
    """Tunable knobs for :func:`spread_activation` (documented defaults)."""

    max_hops: int = 3  # graph radius from the seeds (the hard termination bound)
    decay: float = 0.5  # base per-hop activation multiplier
    min_activation: float = 0.05  # prune a delivery below this
    max_nodes: int = 64  # cap constellation size (cost guard)
    purity_damping: float = 1.0  # exponent on source.purity for its contribution
    edge_weights: Mapping[str, float] = field(default_factory=lambda: dict(_DEFAULT_EDGE_WEIGHTS))
    intent_edges: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(_DEFAULT_INTENT_EDGES)
    )


DEFAULT_ACTIVATION_PARAMS = ActivationParams()


def effective_edge_weight(kind: str, intent: str | None, params: ActivationParams) -> float:
    """Base conductance for ``kind``, gated by the query ``intent``.

    With no intent (or an unrecognized one) every kind conducts at its base weight.
    With a recognized intent, only the preferred kinds conduct (others -> 0.0), so
    the question steers which roots the water follows.
    """
    base = float(params.edge_weights.get(kind, 0.0))
    if intent is None:
        return base
    preferred = params.intent_edges.get(str(intent).lower())
    if preferred is None:
        return base
    return base if kind in preferred else 0.0


def spread_activation(
    seeds: Mapping[str, float],
    neighbors: NeighborsFn,
    states: StatesFn,
    *,
    intent: str | None = None,
    params: ActivationParams = DEFAULT_ACTIVATION_PARAMS,
) -> dict[str, float]:
    """Spread activation from ``seeds`` over the links graph.

    Returns ``{droplet_id: activation}`` for every reached node (seeds included).
    Frontier-based with per-hop decay; a node reached by several paths accumulates.
    Termination is guaranteed by ``max_hops`` (and bounded further by
    ``min_activation`` pruning and the ``max_nodes`` cap).

    Per delivered edge ``src -> dst`` of kind ``k``::

        delivered = src_activation * decay * edge_w(k, intent)
                    * fluidity(src) * (1 - depth(dst)) * purity(src) ** purity_damping
    """
    activation: dict[str, float] = {}
    frontier: dict[str, float] = {}
    for sid, val in seeds.items():
        v = float(val)
        if v <= 0.0:
            continue
        activation[sid] = activation.get(sid, 0.0) + v
        frontier[sid] = activation[sid]

    for _hop in range(max(0, params.max_hops)):
        if not frontier or len(activation) >= params.max_nodes:
            break
        next_frontier: dict[str, float] = {}
        # Deterministic processing order: strongest activation first, then by id.
        for src in sorted(frontier, key=lambda i: (-frontier[i], i)):
            src_state = states(src)
            if src_state is None:
                continue
            conductance = src_state.fluidity
            if conductance <= 0.0:
                continue
            mix = src_state.purity**params.purity_damping
            if mix <= 0.0:
                continue
            src_act = frontier[src]
            for dst, kind in neighbors(src):
                edge_w = effective_edge_weight(kind, intent, params)
                if edge_w <= 0.0:
                    continue
                dst_state = states(dst)
                resistance = dst_state.depth if dst_state is not None else 1.0
                delivered = src_act * params.decay * edge_w * conductance * (1.0 - resistance) * mix
                if delivered < params.min_activation:
                    continue
                activation[dst] = activation.get(dst, 0.0) + delivered
                next_frontier[dst] = next_frontier.get(dst, 0.0) + delivered
        frontier = next_frontier
    return activation


def cluster(
    droplets: Sequence[Droplet],
    neighbors: NeighborsFn,
    *,
    params: ActivationParams = DEFAULT_ACTIVATION_PARAMS,
) -> list[list[Droplet]]:
    """Group ``droplets`` into constellations for consolidation.

    Connected components over the association/support/derived_from subgraph
    (contradictions excluded — see :data:`GROUPING_KINDS`), restricted to the
    given droplet set. This is exactly the ``engine.cluster`` surface the
    DistillationAgent calls; each returned group feeds ``Verbs.distill``.
    Deterministic (components and members are id-sorted). Activation-co-occurrence
    refinement is a future extension; connected components is the reference core.
    """
    by_id: dict[str, Droplet] = {d.id: d for d in droplets}
    ids = set(by_id)
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for i in ids:
        for dst, kind in neighbors(i):
            if kind in GROUPING_KINDS and dst in ids:
                adj[i].add(dst)
                adj[dst].add(i)

    seen: set[str] = set()
    groups: list[list[Droplet]] = []
    for start in sorted(ids):
        if start in seen:
            continue
        component: list[str] = []
        queue: deque[str] = deque([start])
        seen.add(start)
        while queue:
            cur = queue.popleft()
            component.append(cur)
            for nb in sorted(adj[cur]):
                if nb not in seen:
                    seen.add(nb)
                    queue.append(nb)
        groups.append([by_id[c] for c in sorted(component)])
    return groups


__all__ = [
    "ActivationParams",
    "DEFAULT_ACTIVATION_PARAMS",
    "LINK_KINDS",
    "GROUPING_KINDS",
    "effective_edge_weight",
    "spread_activation",
    "cluster",
]
