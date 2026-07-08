"""Storage layer: the DropletRepository contract + the concrete backends.

Backends shipped:

- ``sqlite`` (default): file-backed; uses :class:`SqliteDropletRepository` and
  the brute/ANN vector index sidecar.
- ``postgres``: pgvector-backed; uses :class:`PostgresDropletRepository` from
  ``storage.postgres_repository`` (requires the ``[postgres]`` extra). The
  embedding lives in the table as a ``vector(N)`` column; cosine similarity is
  computed via pgvector's ``<=>`` operator.
"""
from __future__ import annotations

from hydromemory.config import HydroConfig
from hydromemory.storage.repository import DropletRepository
from hydromemory.storage.sqlite_repository import SqliteDropletRepository
from hydromemory.storage.vector_index import VectorIndexProtocol, build_vector_index

__all__ = [
    "DropletRepository",
    "SqliteDropletRepository",
    "open_store",
    "build_vector_index",
    "VectorIndexProtocol",
]


def open_store(config: HydroConfig) -> DropletRepository:
    """Open the droplet store named by ``config.storage_backend``.

    "sqlite" returns the file-backed reference implementation. "postgres" lazily
    imports the pgvector-backed repository so installs without the ``[postgres]``
    extra don't have to carry a psycopg/pgvector dependency.
    """
    backend = (config.storage_backend or "sqlite").lower()
    if backend == "sqlite":
        return SqliteDropletRepository(config)
    if backend == "postgres":
        # Late import: pulls psycopg + pgvector only when explicitly selected.
        from hydromemory.storage.postgres_repository import PostgresDropletRepository

        return PostgresDropletRepository(config)
    raise ValueError(
        f"unknown storage_backend {config.storage_backend!r}; expected 'sqlite' or 'postgres'"
    )
