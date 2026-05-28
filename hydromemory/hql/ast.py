"""HQL abstract syntax tree (PRD §13).

A parsed query is a :class:`Query` with a verb, a target noun, a conjunction of
:class:`Predicate` rows, and optional ``GROUP BY`` / ``OUTPUT`` clauses (an
:class:`OutputSpec`). HQL is conjunction-only (``AND``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Predicate:
    """A single ``field OP value`` condition (or a ``permission.allows(agent)`` call).

    For the function form ``permission.allows("assistant")`` the parser emits
    ``field="permission.allows"``, ``op="call"``, ``value="assistant"``.
    """

    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class OutputSpec:
    """An ``OUTPUT`` clause (e.g. ``OUTPUT principle``)."""

    spec: str


@dataclass
class Query:
    """A parsed HQL statement."""

    verb: str                       # GET | PRECIPITATE | FILTER | DISTILL
    target: str                     # memories | cloud | ...
    predicates: list[Predicate] = field(default_factory=list)
    group_by: str | None = None
    output: OutputSpec | None = None

    def predicate_map(self) -> dict[str, Predicate]:
        """Index predicates by field name (last write wins for duplicates)."""
        return {p.field: p for p in self.predicates}
