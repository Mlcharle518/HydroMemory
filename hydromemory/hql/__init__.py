"""Hydro Query Language (PRD §13).

A small conjunction-only DSL over the droplet store. ``parse`` builds an AST
(:class:`~hydromemory.hql.ast.Query`); ``execute`` runs it against a
:class:`~hydromemory.storage.repository.DropletRepository`.
"""
from __future__ import annotations

from hydromemory.hql.ast import OutputSpec, Predicate, Query
from hydromemory.hql.executor import compile_filters, compile_precipitate, execute
from hydromemory.hql.lexer import HQLSyntaxError, tokenize
from hydromemory.hql.parser import parse

__all__ = [
    "parse",
    "execute",
    "Query",
    "Predicate",
    "OutputSpec",
    "tokenize",
    "compile_precipitate",
    "compile_filters",
    "HQLSyntaxError",
]
