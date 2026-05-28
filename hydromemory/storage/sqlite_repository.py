"""SQLite + file-backed vector-index implementation of :class:`DropletRepository`.

This is the Track A concrete DAO (PRD §5.3, §7, §14). The hybrid SQLite schema
(see :mod:`hydromemory.storage.db`) holds the queryable droplet dimensions as
columns plus the rest as JSON; the ``links`` table is the source of truth for
the droplet graph; and a brute-force cosine :class:`VectorIndex` (a rebuildable
cache) backs ``search_similar``. The repository only returns candidate droplets
and cosine similarity — recall scoring (§5.6) is the engine's job.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from hydromemory.config import HydroConfig
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Links, Phase, Visibility, _fmt_dt
from hydromemory.storage.db import connect, init_schema
from hydromemory.storage.repository import DropletRepository
from hydromemory.storage.vector_index import build_vector_index

# The four link kinds (mirror :class:`hydromemory.schema.Links` fields).
_LINK_KINDS: tuple[str, ...] = ("associations", "contradictions", "supports", "derived_from")

# Reserved meta key used to persist the embedding in ``meta_json`` (the frozen
# column schema has no embedding column). It is stripped back out on read so a
# round-tripped ``droplet.meta`` never exposes this internal key, while the
# embedding survives reopen and powers ``rebuild_index`` from stored rows.
_EMBED_META_KEY = "__embedding__"


class SqliteDropletRepository(DropletRepository):
    """Persistent droplet store backed by SQLite + a file-backed vector index.

    The vector index is persisted next to the DB at ``f"{db_path}.vec.npz"`` and
    loaded on construction, so reopening a new ``SqliteDropletRepository`` on the
    same ``db_path`` recovers both the rows and the embeddings.
    """

    def __init__(self, config: HydroConfig) -> None:
        self.config = config
        self.db_path = config.db_path
        self._conn: sqlite3.Connection = connect(self.db_path)
        init_schema(self._conn)
        self._index = build_vector_index(
            f"{self.db_path}.vec.npz", config.vector_dim, backend=config.vector_backend
        )
        self._index.load()

    # ------------------------------------------------------------------ helpers
    def _row_to_droplet(self, row: sqlite3.Row) -> Droplet:
        """Reassemble a Droplet from a row, restoring links from the links table."""
        meta: dict[str, Any] = json.loads(row["meta_json"] or "{}")
        embedding = meta.pop(_EMBED_META_KEY, None)
        assembled: dict[str, Any] = {
            "id": row["id"],
            "content": row["content"],
            "source": row["source"],
            "created_at": row["created_at"],
            "phase": row["phase"],
            "reservoir": row["reservoir"],
            "memory_type": row["memory_type"],
            "semantic_tags": json.loads(row["semantic_tags_json"] or "[]"),
            "state": json.loads(row["state_json"] or "{}"),
            "permissions": json.loads(row["permissions_json"] or "{}"),
            "cycle": json.loads(row["cycle_json"] or "{}"),
            "meta": meta,
            "embedding": embedding,
        }
        droplet = Droplet.from_dict(assembled)
        droplet.links = self._load_links(droplet.id)
        return droplet

    def _load_links(self, src_id: str) -> Links:
        """Build a Links object for ``src_id`` from the links table (source of truth)."""
        buckets: dict[str, list[str]] = {kind: [] for kind in _LINK_KINDS}
        cur = self._conn.execute(
            "SELECT kind, dst_id FROM links WHERE src_id = ? ORDER BY rowid", (src_id,)
        )
        for r in cur.fetchall():
            kind = r["kind"]
            if kind in buckets:
                buckets[kind].append(r["dst_id"])
        return Links.from_dict(buckets)

    def _sync_links(self, droplet: Droplet) -> None:
        """Replace the link rows for ``droplet`` so the table matches droplet.links."""
        self._conn.execute("DELETE FROM links WHERE src_id = ?", (droplet.id,))
        link_map = droplet.links.to_dict()
        rows = [
            (droplet.id, kind, dst)
            for kind in _LINK_KINDS
            for dst in link_map.get(kind, [])
        ]
        if rows:
            self._conn.executemany(
                "INSERT OR IGNORE INTO links (src_id, kind, dst_id) VALUES (?, ?, ?)", rows
            )

    # ------------------------------------------------------------------- CRUD
    def upsert(self, droplet: Droplet) -> None:
        data = droplet.to_dict()
        perms = data["permissions"]
        state = data["state"]
        # Persist the embedding inside meta_json (no embedding column exists), so
        # it survives reopen and rebuild_index can reload it from the row.
        persisted_meta: dict[str, Any] = dict(data.get("meta", {}))
        if droplet.embedding is not None:
            persisted_meta[_EMBED_META_KEY] = list(droplet.embedding)
        else:
            persisted_meta.pop(_EMBED_META_KEY, None)
        self._conn.execute(
            """
            INSERT INTO droplets (
                id, content, source, created_at, phase, reservoir, memory_type,
                owner, visibility, retention, external_sharing, purity,
                state_json, permissions_json, semantic_tags_json, cycle_json, meta_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                content=excluded.content,
                source=excluded.source,
                created_at=excluded.created_at,
                phase=excluded.phase,
                reservoir=excluded.reservoir,
                memory_type=excluded.memory_type,
                owner=excluded.owner,
                visibility=excluded.visibility,
                retention=excluded.retention,
                external_sharing=excluded.external_sharing,
                purity=excluded.purity,
                state_json=excluded.state_json,
                permissions_json=excluded.permissions_json,
                semantic_tags_json=excluded.semantic_tags_json,
                cycle_json=excluded.cycle_json,
                meta_json=excluded.meta_json
            """,
            (
                data["id"],
                data["content"],
                data["source"],
                data["created_at"],
                data["phase"],
                data["reservoir"],
                data["memory_type"],
                perms["owner"],
                perms["visibility"],
                perms["retention"],
                1 if perms["external_sharing"] else 0,
                float(state["purity"]),
                json.dumps(state),
                json.dumps(perms),
                json.dumps(data["semantic_tags"]),
                json.dumps(data["cycle"]),
                json.dumps(persisted_meta),
            ),
        )
        self._sync_links(droplet)
        if droplet.embedding is not None:
            self._index.add(droplet.id, droplet.embedding)
        else:
            # An explicit embedding-less upsert should not leave a stale vector.
            self._index.remove(droplet.id)
        self._conn.commit()
        self._index.persist()

    def get(self, droplet_id: str) -> Droplet | None:
        row = self._conn.execute(
            "SELECT * FROM droplets WHERE id = ?", (droplet_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_droplet(row)

    def delete(self, droplet_id: str) -> None:
        self._conn.execute("DELETE FROM droplets WHERE id = ?", (droplet_id,))
        self._conn.execute("DELETE FROM links WHERE src_id = ?", (droplet_id,))
        self._conn.execute("DELETE FROM links WHERE dst_id = ?", (droplet_id,))
        self._index.remove(droplet_id)
        self._conn.commit()
        self._index.persist()

    def all_ids(self) -> list[str]:
        cur = self._conn.execute("SELECT id FROM droplets ORDER BY rowid")
        return [r["id"] for r in cur.fetchall()]

    # ------------------------------------------------------------------ query
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
        """Filter droplets over the indexed columns.

        ``usable_for_response_only`` excludes the contaminated reservoir and the
        polluted phase (memory that must be filtered before use, PRD §10.1).

        ``allowed_agent`` is an access convenience kept deliberately simple: a
        droplet matches if the agent is listed in its permissions'
        ``allowed_agents``, OR the droplet is owner ``"user"`` with ``public``
        visibility (the §10-style "anyone may read public user memory" shortcut).
        The authoritative access policy lives in Track C governance; this is only
        a coarse pre-filter so callers can fetch a sensible candidate set.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if reservoir is not None:
            clauses.append("reservoir = ?")
            params.append(reservoir.value)
        if phase is not None:
            clauses.append("phase = ?")
            params.append(phase.value)
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if min_purity is not None:
            clauses.append("purity >= ?")
            params.append(float(min_purity))
        if visibility is not None:
            clauses.append("visibility = ?")
            params.append(visibility.value)
        if usable_for_response_only:
            clauses.append("reservoir != ?")
            params.append(Reservoir.CONTAMINATED.value)
            clauses.append("phase != ?")
            params.append(Phase.POLLUTED.value)

        sql = "SELECT * FROM droplets"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY rowid"

        rows = self._conn.execute(sql, params).fetchall()
        results: list[Droplet] = []
        for row in rows:
            if allowed_agent is not None and not self._agent_allowed(row, allowed_agent):
                continue
            results.append(self._row_to_droplet(row))
            if limit is not None and len(results) >= limit:
                break
        return results

    @staticmethod
    def _agent_allowed(row: sqlite3.Row, agent: str) -> bool:
        """True if ``agent`` is in allowed_agents, or the row is public user memory."""
        perms = json.loads(row["permissions_json"] or "{}")
        if agent in (perms.get("allowed_agents") or []):
            return True
        return perms.get("owner") == "user" and perms.get("visibility") == Visibility.PUBLIC.value

    # ----------------------------------------------------------- similarity
    def search_similar(
        self,
        embedding: Sequence[float],
        k: int = 10,
        candidate_filter: Callable[[Droplet], bool] | None = None,
    ) -> list[tuple[str, float]]:
        """Return up to ``k`` ``(id, cosine)`` pairs, honoring ``candidate_filter``.

        Without a filter, the index's top-``k`` is returned directly. With a
        filter, we over-fetch from the index, load each candidate droplet, keep
        those for which ``candidate_filter(droplet)`` is True, and truncate to
        ``k`` (preserving cosine order).
        """
        if candidate_filter is None:
            return self._index.search(embedding, k)
        # Over-fetch (whole index) then apply the predicate against full droplets.
        ranked = self._index.search(embedding, len(self._index))
        out: list[tuple[str, float]] = []
        for did, cos in ranked:
            droplet = self.get(did)
            if droplet is None:
                continue
            if candidate_filter(droplet):
                out.append((did, cos))
                if len(out) >= k:
                    break
        return out

    # ---------------------------------------------------------------- links
    def add_link(self, src_id: str, kind: str, dst_id: str) -> None:
        if kind not in _LINK_KINDS:
            raise ValueError(f"unknown link kind {kind!r}; expected one of {_LINK_KINDS}")
        self._conn.execute(
            "INSERT OR IGNORE INTO links (src_id, kind, dst_id) VALUES (?, ?, ?)",
            (src_id, kind, dst_id),
        )
        self._conn.commit()

    def remove_link(self, src_id: str, kind: str, dst_id: str) -> None:
        self._conn.execute(
            "DELETE FROM links WHERE src_id = ? AND kind = ? AND dst_id = ?",
            (src_id, kind, dst_id),
        )
        self._conn.commit()

    # ---------------------------------------------------------------- cycle
    def touch_cycle(
        self,
        droplet_id: str,
        *,
        recalled: datetime | None = None,
        transformed: datetime | None = None,
        verified: datetime | None = None,
        increment_count: bool = False,
    ) -> None:
        """Update cycle metadata in place (only the provided fields change)."""
        row = self._conn.execute(
            "SELECT cycle_json FROM droplets WHERE id = ?", (droplet_id,)
        ).fetchone()
        if row is None:
            return
        cycle: dict[str, Any] = json.loads(row["cycle_json"] or "{}")
        if recalled is not None:
            cycle["last_recalled"] = _fmt_dt(recalled)
        if transformed is not None:
            cycle["last_transformed"] = _fmt_dt(transformed)
        if verified is not None:
            cycle["last_verified"] = _fmt_dt(verified)
        if increment_count:
            cycle["cycle_count"] = int(cycle.get("cycle_count", 0) or 0) + 1
        self._conn.execute(
            "UPDATE droplets SET cycle_json = ? WHERE id = ?",
            (json.dumps(cycle), droplet_id),
        )
        self._conn.commit()

    # --------------------------------------------------------------- index
    def rebuild_index(self) -> None:
        """Rebuild the vector index from every stored droplet's embedding."""
        rows = self._conn.execute("SELECT * FROM droplets ORDER BY rowid").fetchall()
        items: list[tuple[str, object]] = []
        for row in rows:
            droplet = self._row_to_droplet(row)
            if droplet.embedding is not None:
                items.append((droplet.id, droplet.embedding))
        self._index.rebuild(items)
        self._index.persist()

    def close(self) -> None:
        self._index.persist()
        self._conn.close()
