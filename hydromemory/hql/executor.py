"""HQL executor (PRD §13).

Maps a parsed :class:`~hydromemory.hql.ast.Query` to repository / engine calls:

* ``GET``        -> :meth:`DropletRepository.query` (indexed filters) + post-fetch
                    filters (``permission.allows``, ``related_to``, ...).
* ``PRECIPITATE``-> compiles to the §13 JSON op and (if a ``recall`` callable is
                    supplied) runs the recall path.
* ``FILTER``     -> fetch matching droplets, then ``verbs.filter`` each.
* ``DISTILL``    -> group matching droplets and ``verbs.distill`` a principle.

Predicates are partitioned into *repository filters* (the indexed query
dimensions: ``reservoir``/``type``|``memory_type``/``phase``/``purity``/``topic``)
versus *post-fetch filters* (``permission.allows``, ``related_to``,
``minimum_purity``, ``maximum_privacy_risk``, ``theme``, ``trigger``).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hydromemory.hql.ast import Predicate, Query
from hydromemory.hql.lexer import HQLSyntaxError
from hydromemory.reservoirs import Reservoir, normalize_reservoir
from hydromemory.schema import Droplet, Phase
from hydromemory.storage.repository import DropletRepository

# Predicate fields that map directly onto repository query kwargs.
_REPO_FIELDS = {"reservoir", "type", "memory_type", "phase", "purity", "topic"}


@dataclass
class CompiledQuery:
    """A query split into repository-level kwargs and post-fetch predicates."""

    repo_kwargs: dict[str, Any]
    post_filters: list[Predicate]
    topic: str | None = None


def _coerce_reservoir(value: Any) -> Reservoir:
    return normalize_reservoir(value)


def _coerce_phase(value: Any) -> Phase:
    return Phase(str(value))


def compile_filters(query: Query) -> CompiledQuery:
    """Partition ``query`` predicates into repo kwargs vs post-fetch predicates."""
    repo_kwargs: dict[str, Any] = {}
    post: list[Predicate] = []
    topic: str | None = None

    for pred in query.predicates:
        field = pred.field
        if field == "reservoir" and pred.op == "=":
            repo_kwargs["reservoir"] = _coerce_reservoir(pred.value)
        elif field in ("type", "memory_type") and pred.op == "=":
            repo_kwargs["memory_type"] = str(pred.value)
        elif field == "phase" and pred.op == "=":
            repo_kwargs["phase"] = _coerce_phase(pred.value)
        elif field == "purity" and pred.op in (">", ">="):
            # repo supports a min_purity floor; strict '>' is re-checked post-fetch.
            repo_kwargs["min_purity"] = float(pred.value)
            if pred.op == ">":
                post.append(pred)
        elif field == "topic" and pred.op == "=":
            topic = str(pred.value)
        else:
            post.append(pred)

    return CompiledQuery(repo_kwargs=repo_kwargs, post_filters=post, topic=topic)


def _droplet_passes(droplet: Droplet, pred: Predicate) -> bool:
    """Evaluate a post-fetch predicate against a droplet."""
    field = pred.field

    if field == "permission.allows" and pred.op == "call":
        agent = str(pred.value)
        perms = droplet.permissions
        if perms.visibility.value == "public":
            return True
        return (not perms.allowed_agents) or (agent in perms.allowed_agents)

    if field == "related_to":
        target = str(pred.value).lower()
        tags = {str(t).lower() for t in droplet.semantic_tags}
        if target in tags:
            return True
        links = (
            droplet.links.associations
            + droplet.links.supports
            + droplet.links.derived_from
            + droplet.links.contradictions
        )
        if any(target == str(link_id).lower() for link_id in links):
            return True
        ctx = droplet.meta.get("context")
        if isinstance(ctx, dict) and target == str(ctx.get("topic", "")).lower():
            return True
        return target in droplet.content.lower()

    if field == "purity":
        return _numeric_cmp(droplet.state.purity, pred.op, float(pred.value))

    if field in ("minimum_purity",):
        return droplet.state.purity >= float(pred.value)

    # Generic state-float field comparison (e.g. confidence, gravity).
    if hasattr(droplet.state, field):
        return _numeric_cmp(getattr(droplet.state, field), pred.op, float(pred.value))

    # Topic/theme/trigger handled at the query level (PRECIPITATE), tolerate here.
    if field in ("theme", "trigger", "topic"):
        return True

    raise HQLSyntaxError(f"unsupported predicate field {field!r}")


def _numeric_cmp(actual: float, op: str, expected: float) -> bool:
    if op == "=":
        return actual == expected
    if op == ">":
        return actual > expected
    if op == "<":
        return actual < expected
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    if op == "!=":
        return actual != expected
    raise HQLSyntaxError(f"unsupported operator {op!r}")


def compile_precipitate(query: Query) -> dict[str, Any]:
    """Compile a ``PRECIPITATE`` query to the §13 JSON op.

    Shape::

        {"operation": "PRECIPITATE",
         "query": {"theme", "trigger", "reservoirs",
                   "minimum_purity", "maximum_privacy_risk"},
         "output": {"mode", "include_explanation"}}
    """
    pmap = query.predicate_map()
    inner: dict[str, Any] = {}
    if "theme" in pmap:
        inner["theme"] = pmap["theme"].value
    if "trigger" in pmap:
        inner["trigger"] = pmap["trigger"].value
    if "reservoir" in pmap:
        inner["reservoirs"] = [str(_coerce_reservoir(pmap["reservoir"].value).value)]
    if "reservoirs" in pmap:
        val = pmap["reservoirs"].value
        inner["reservoirs"] = list(val) if isinstance(val, (list, tuple)) else [str(val)]
    if "minimum_purity" in pmap:
        inner["minimum_purity"] = float(pmap["minimum_purity"].value)
    elif "purity" in pmap and pmap["purity"].op in (">", ">="):
        inner["minimum_purity"] = float(pmap["purity"].value)
    if "maximum_privacy_risk" in pmap:
        inner["maximum_privacy_risk"] = float(pmap["maximum_privacy_risk"].value)

    return {
        "operation": "PRECIPITATE",
        "query": inner,
        "output": {"mode": "behavioral_guidance", "include_explanation": False},
    }


def execute(
    query: Query,
    repo: DropletRepository,
    *,
    recall: Callable[[dict[str, Any]], Any] | None = None,
    verbs: Any = None,
) -> Any:
    """Execute ``query`` against ``repo`` (and optionally ``recall``/``verbs``)."""
    verb = query.verb

    if verb == "GET":
        return _execute_get(query, repo)

    if verb == "PRECIPITATE":
        op = compile_precipitate(query)
        if recall is not None:
            return recall(op)
        return op

    if verb == "FILTER":
        if verbs is None:
            raise HQLSyntaxError("FILTER execution requires a Verbs instance")
        matches = _execute_get(query, repo)
        return [verbs.filter(d) for d in matches]

    if verb == "DISTILL":
        if verbs is None:
            raise HQLSyntaxError("DISTILL execution requires a Verbs instance")
        matches = _execute_get(query, repo)
        if not matches:
            return None
        return verbs.distill(matches)

    raise HQLSyntaxError(f"cannot execute verb {verb!r}")


def _execute_get(query: Query, repo: DropletRepository) -> list[Droplet]:
    """Run the repository query then apply post-fetch predicate filters."""
    compiled = compile_filters(query)
    candidates = repo.query(**compiled.repo_kwargs)

    results: list[Droplet] = []
    for droplet in candidates:
        if compiled.topic is not None and not _topic_matches(droplet, compiled.topic):
            continue
        if all(_droplet_passes(droplet, pred) for pred in compiled.post_filters):
            results.append(droplet)
    return results


def _topic_matches(droplet: Droplet, topic: str) -> bool:
    topic_l = topic.lower()
    tags = {str(t).lower() for t in droplet.semantic_tags}
    if topic_l in tags or any(topic_l in t or t in topic_l for t in tags):
        return True
    ctx = droplet.meta.get("context")
    if isinstance(ctx, dict) and topic_l == str(ctx.get("topic", "")).lower():
        return True
    return topic_l in droplet.content.lower()
