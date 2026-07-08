"""Postgres + pgvector implementation of :class:`DropletRepository`.

Mirrors :class:`SqliteDropletRepository` row-for-row so the engine treats either
backend the same. Differences worth knowing:

- The embedding lives **in the row** as a ``vector(N)`` column (sized by
  ``config.vector_dim``) instead of a sidecar ``.vec.npz`` file. ``search_similar``
  is a single ``ORDER BY embedding <=> %s LIMIT k`` query — pgvector returns
  cosine *distance*, which we convert to similarity via ``1 - distance``.
- JSON sidecar columns are ``JSONB``; ``external_sharing`` is BOOLEAN; ordering
  uses a ``seq BIGSERIAL`` column (Postgres has no implicit rowid).
- The ``links`` table is identical to SQLite's.

Vault key-management and other auxiliary code paths that talk directly to
``config.db_path`` (the SQLite file) are not Postgres-aware; they stay on SQLite
in this PR. The Postgres backend covers the **droplet store** end-to-end.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from hydromemory.config import HydroConfig
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Links, Phase, Visibility, _fmt_dt
from hydromemory.storage.repository import DropletRepository

if TYPE_CHECKING:
    import psycopg

_LINK_KINDS: tuple[str, ...] = ("associations", "contradictions", "supports", "derived_from")


def _check_deps() -> None:
    """Raise a friendly error when the Postgres extras aren't installed."""
    try:
        import pgvector.psycopg  # noqa: F401
        import psycopg  # noqa: F401
    except ImportError as exc:  # pragma: no cover - covered indirectly by the install check
        raise RuntimeError(
            "Postgres backend requires the [postgres] extra. "
            "Install with: pip install -e '.[postgres]'"
        ) from exc


class PostgresDropletRepository(DropletRepository):
    """Persistent droplet store backed by Postgres + pgvector."""

    def __init__(self, config: HydroConfig) -> None:
        if not config.database_url:
            raise ValueError(
                "Postgres backend requires database_url (set HYDRO_DATABASE_URL)"
            )
        _check_deps()
        from pgvector.psycopg import register_vector
        from psycopg import connect

        self.config = config
        self._dim = int(config.vector_dim)
        self._conn: psycopg.Connection = connect(config.database_url, autocommit=False)
        # CREATE EXTENSION must be on its own transaction before we can use the type.
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self._conn.commit()
        register_vector(self._conn)
        self._init_schema()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        """Create the droplets + links tables (and a vector index) if absent.

        The vector column is sized by ``config.vector_dim``; pgvector requires a
        compile-time-known dim per column. If a pre-existing table has a
        different dim, that's reported up with a clear error rather than silently
        accepting mismatched embeddings.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS droplets (
                    seq               BIGSERIAL,
                    id                TEXT PRIMARY KEY,
                    content           TEXT,
                    source            TEXT,
                    created_at        TEXT,
                    phase             TEXT,
                    reservoir         TEXT,
                    memory_type       TEXT,
                    owner             TEXT,
                    visibility        TEXT,
                    retention         TEXT,
                    external_sharing  BOOLEAN,
                    purity            DOUBLE PRECISION,
                    state_json        JSONB,
                    permissions_json  JSONB,
                    semantic_tags_json JSONB,
                    cycle_json        JSONB,
                    meta_json         JSONB,
                    app_id            TEXT,
                    embedding         vector({self._dim})
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    src_id TEXT NOT NULL,
                    kind   TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    UNIQUE (src_id, kind, dst_id)
                )
                """
            )
            for stmt in (
                "CREATE INDEX IF NOT EXISTS idx_droplets_reservoir ON droplets (reservoir)",
                "CREATE INDEX IF NOT EXISTS idx_droplets_phase ON droplets (phase)",
                "CREATE INDEX IF NOT EXISTS idx_droplets_memory_type ON droplets (memory_type)",
                "CREATE INDEX IF NOT EXISTS idx_droplets_visibility ON droplets (visibility)",
                "CREATE INDEX IF NOT EXISTS idx_droplets_purity ON droplets (purity)",
                "CREATE INDEX IF NOT EXISTS idx_droplets_app_id ON droplets (app_id)",
                "CREATE INDEX IF NOT EXISTS idx_links_src ON links (src_id)",
                "CREATE INDEX IF NOT EXISTS idx_links_dst ON links (dst_id)",
            ):
                cur.execute(stmt)
            # Detect vector_dim mismatch on a pre-existing table — pgvector will
            # otherwise reject inserts with a confusing "expected N dimensions" error
            # buried inside the executemany. Surface it up front instead.
            cur.execute(
                """
                SELECT atttypmod FROM pg_attribute
                WHERE attrelid = 'droplets'::regclass AND attname = 'embedding'
                """
            )
            row = cur.fetchone()
            if row is not None and row[0] not in (None, -1, self._dim):
                raise RuntimeError(
                    f"existing droplets.embedding has dim={row[0]}, "
                    f"but config.vector_dim={self._dim}. Migrate the column or "
                    "set HYDRO_EMBED_DIM to match."
                )
        self._conn.commit()

    # ----------------------------------------------------------------- helpers
    def _row_to_droplet(self, row: dict[str, Any]) -> Droplet:
        embedding = row["embedding"]
        if embedding is not None:
            # pgvector returns a numpy array; normalise to list[float] for the schema.
            embedding = list(embedding)
        assembled: dict[str, Any] = {
            "id": row["id"],
            "content": row["content"],
            "source": row["source"],
            "created_at": row["created_at"],
            "phase": row["phase"],
            "reservoir": row["reservoir"],
            "memory_type": row["memory_type"],
            "semantic_tags": row["semantic_tags_json"] or [],
            "state": row["state_json"] or {},
            "permissions": row["permissions_json"] or {},
            "cycle": row["cycle_json"] or {},
            "meta": row["meta_json"] or {},
            "embedding": embedding,
        }
        droplet = Droplet.from_dict(assembled)
        droplet.links = self._load_links(droplet.id)
        return droplet

    def _load_links(self, src_id: str) -> Links:
        buckets: dict[str, list[str]] = {kind: [] for kind in _LINK_KINDS}
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT kind, dst_id FROM links WHERE src_id = %s ORDER BY ctid",
                (src_id,),
            )
            for kind, dst in cur.fetchall():
                if kind in buckets:
                    buckets[kind].append(dst)
        return Links.from_dict(buckets)

    def _sync_links(self, droplet: Droplet) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM links WHERE src_id = %s", (droplet.id,))
            link_map = droplet.links.to_dict()
            rows = [
                (droplet.id, kind, dst)
                for kind in _LINK_KINDS
                for dst in link_map.get(kind, [])
            ]
            if rows:
                cur.executemany(
                    "INSERT INTO links (src_id, kind, dst_id) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    rows,
                )

    @staticmethod
    def _agent_allowed(row: dict[str, Any], agent: str) -> bool:
        perms = row.get("permissions_json") or {}
        if agent in (perms.get("allowed_agents") or []):
            return True
        return perms.get("owner") == "user" and perms.get("visibility") == Visibility.PUBLIC.value

    # ------------------------------------------------------------------- CRUD
    def upsert(self, droplet: Droplet) -> None:
        data = droplet.to_dict()
        perms = data["permissions"]
        state = data["state"]
        embedding = list(droplet.embedding) if droplet.embedding is not None else None
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO droplets (
                    id, content, source, created_at, phase, reservoir, memory_type,
                    owner, visibility, retention, external_sharing, purity,
                    state_json, permissions_json, semantic_tags_json, cycle_json,
                    meta_json, embedding
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    content=EXCLUDED.content,
                    source=EXCLUDED.source,
                    created_at=EXCLUDED.created_at,
                    phase=EXCLUDED.phase,
                    reservoir=EXCLUDED.reservoir,
                    memory_type=EXCLUDED.memory_type,
                    owner=EXCLUDED.owner,
                    visibility=EXCLUDED.visibility,
                    retention=EXCLUDED.retention,
                    external_sharing=EXCLUDED.external_sharing,
                    purity=EXCLUDED.purity,
                    state_json=EXCLUDED.state_json,
                    permissions_json=EXCLUDED.permissions_json,
                    semantic_tags_json=EXCLUDED.semantic_tags_json,
                    cycle_json=EXCLUDED.cycle_json,
                    meta_json=EXCLUDED.meta_json,
                    embedding=EXCLUDED.embedding
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
                    bool(perms["external_sharing"]),
                    float(state["purity"]),
                    json.dumps(state),
                    json.dumps(perms),
                    json.dumps(data["semantic_tags"]),
                    json.dumps(data["cycle"]),
                    json.dumps(data.get("meta", {})),
                    embedding,
                ),
            )
        self._sync_links(droplet)
        self._conn.commit()

    def get(self, droplet_id: str) -> Droplet | None:
        row = self._fetch_one("SELECT * FROM droplets WHERE id = %s", (droplet_id,))
        if row is None:
            return None
        return self._row_to_droplet(row)

    def delete(self, droplet_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM droplets WHERE id = %s", (droplet_id,))
            cur.execute("DELETE FROM links WHERE src_id = %s OR dst_id = %s", (droplet_id, droplet_id))
        self._conn.commit()

    def all_ids(self) -> list[str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id FROM droplets ORDER BY seq")
            return [r[0] for r in cur.fetchall()]

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
        clauses: list[str] = []
        params: list[Any] = []
        if reservoir is not None:
            clauses.append("reservoir = %s")
            params.append(reservoir.value)
        if phase is not None:
            clauses.append("phase = %s")
            params.append(phase.value)
        if memory_type is not None:
            clauses.append("memory_type = %s")
            params.append(memory_type)
        if min_purity is not None:
            clauses.append("purity >= %s")
            params.append(float(min_purity))
        if visibility is not None:
            clauses.append("visibility = %s")
            params.append(visibility.value)
        if usable_for_response_only:
            clauses.append("reservoir <> %s")
            params.append(Reservoir.CONTAMINATED.value)
            clauses.append("phase <> %s")
            params.append(Phase.POLLUTED.value)
        sql = "SELECT * FROM droplets"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq"
        results: list[Droplet] = []
        for row in self._fetch_all(sql, tuple(params)):
            if allowed_agent is not None and not self._agent_allowed(row, allowed_agent):
                continue
            results.append(self._row_to_droplet(row))
            if limit is not None and len(results) >= limit:
                break
        return results

    # ----------------------------------------------------------- similarity
    def search_similar(
        self,
        embedding: Sequence[float],
        k: int = 10,
        candidate_filter: Callable[[Droplet], bool] | None = None,
    ) -> list[tuple[str, float]]:
        """Return up to ``k`` (id, cosine_similarity) pairs.

        pgvector's ``<=>`` returns cosine *distance*; we yield ``1 - distance`` so
        the contract matches the SQLite repo's similarity score. Rows without an
        embedding are excluded via the IS NOT NULL filter.
        """
        query_vec = list(embedding)
        # Without a candidate filter we just take the top-k from Postgres directly.
        # With a filter, over-fetch (all rows with embeddings) and trim in Python
        # to match the SQLite repo's behaviour.
        with self._conn.cursor() as cur:
            if candidate_filter is None:
                cur.execute(
                    "SELECT id, embedding <=> %s::vector AS dist FROM droplets "
                    "WHERE embedding IS NOT NULL ORDER BY dist LIMIT %s",
                    (query_vec, k),
                )
                return [(rid, 1.0 - float(dist)) for rid, dist in cur.fetchall()]
            cur.execute(
                "SELECT id, embedding <=> %s::vector AS dist FROM droplets "
                "WHERE embedding IS NOT NULL ORDER BY dist",
                (query_vec,),
            )
            ranked = cur.fetchall()
        out: list[tuple[str, float]] = []
        for rid, dist in ranked:
            droplet = self.get(rid)
            if droplet is None:
                continue
            if candidate_filter(droplet):
                out.append((rid, 1.0 - float(dist)))
                if len(out) >= k:
                    break
        return out

    # ---------------------------------------------------------------- links
    def add_link(self, src_id: str, kind: str, dst_id: str) -> None:
        if kind not in _LINK_KINDS:
            raise ValueError(f"unknown link kind {kind!r}; expected one of {_LINK_KINDS}")
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO links (src_id, kind, dst_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (src_id, kind, dst_id),
            )
        self._conn.commit()

    def remove_link(self, src_id: str, kind: str, dst_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM links WHERE src_id = %s AND kind = %s AND dst_id = %s",
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
        row = self._fetch_one(
            "SELECT cycle_json FROM droplets WHERE id = %s", (droplet_id,)
        )
        if row is None:
            return
        cycle: dict[str, Any] = dict(row["cycle_json"] or {})
        if recalled is not None:
            cycle["last_recalled"] = _fmt_dt(recalled)
        if transformed is not None:
            cycle["last_transformed"] = _fmt_dt(transformed)
        if verified is not None:
            cycle["last_verified"] = _fmt_dt(verified)
        if increment_count:
            cycle["cycle_count"] = int(cycle.get("cycle_count", 0) or 0) + 1
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE droplets SET cycle_json = %s WHERE id = %s",
                (json.dumps(cycle), droplet_id),
            )
        self._conn.commit()

    # --------------------------------------------------------------- index
    def rebuild_index(self) -> None:
        """No-op for pgvector: the embedding lives in the row, not a sidecar cache."""
        return

    def close(self) -> None:
        self._conn.close()

    # --------------------------------------------------------------- internals
    def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d.name for d in cur.description]
            return dict(zip(cols, row, strict=True))

    def _fetch_all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
