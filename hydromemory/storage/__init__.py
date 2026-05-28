"""Storage layer: the DropletRepository contract + the concrete SQLite impl."""
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
    """Open the configured droplet store (SQLite-backed in this reference impl)."""
    return SqliteDropletRepository(config)
