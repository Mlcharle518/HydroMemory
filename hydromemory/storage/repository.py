"""DropletRepository DAO contract (PRD §5.3, §7, §14).

The concrete SQLite + file-backed vector-index implementation lands in Phase 1
(Track A) as ``storage/sqlite_repository.py``. This module freezes the interface
the engine/recall/pipeline depend on. The repository returns candidate droplets
plus cosine similarity only — recall scoring (§5.6) is computed by the engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from datetime import datetime

from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, Visibility


class DropletRepository(ABC):
    @abstractmethod
    def upsert(self, droplet: Droplet) -> None:
        """Insert or update a droplet (and its embedding, if present)."""

    @abstractmethod
    def get(self, droplet_id: str) -> Droplet | None:
        ...

    @abstractmethod
    def delete(self, droplet_id: str) -> None:
        """Hard-delete a droplet (the FORGET verb's delete path)."""

    @abstractmethod
    def all_ids(self) -> list[str]:
        ...

    @abstractmethod
    def query(
        self,
        *,
        reservoir: Reservoir | None = None,
        phase: Phase | None = None,
        memory_type: str | None = None,
        min_purity: float | None = None,
        visibility: Visibility | None = None,
        allowed_agent: str | None = None,
        usable_for_response_only: bool = False,
        limit: int | None = None,
    ) -> list[Droplet]:
        """Filter droplets by the indexed query dimensions."""

    @abstractmethod
    def search_similar(
        self,
        embedding: Sequence[float],
        k: int = 10,
        candidate_filter: Callable[[Droplet], bool] | None = None,
    ) -> list[tuple[str, float]]:
        """Return up to ``k`` (droplet_id, cosine) pairs, honoring ``candidate_filter``."""

    @abstractmethod
    def add_link(self, src_id: str, kind: str, dst_id: str) -> None:
        """Add a directed link edge (associations|contradictions|supports|derived_from)."""

    @abstractmethod
    def remove_link(self, src_id: str, kind: str, dst_id: str) -> None:
        ...

    @abstractmethod
    def touch_cycle(
        self,
        droplet_id: str,
        *,
        recalled: datetime | None = None,
        transformed: datetime | None = None,
        verified: datetime | None = None,
        increment_count: bool = False,
    ) -> None:
        """Update cycle metadata (last_recalled/transformed/verified, cycle_count)."""

    @abstractmethod
    def rebuild_index(self) -> None:
        """Rebuild the vector index from stored droplets (the index is a cache)."""

    @abstractmethod
    def close(self) -> None:
        ...
