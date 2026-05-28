"""SQLite connection + schema bootstrap (stdlib ``sqlite3`` only, no ORM).

The on-disk model is *hybrid* (PRD §7): the queryable droplet dimensions are
promoted to typed columns (so the indexed ``query`` filters are plain SQL),
while the rest of the droplet — its full state vector, permissions, semantic
tags, link graph, cycle metadata, and any preserved unknown keys — is stored as
JSON blobs alongside. The companion ``links`` table is the source of truth for
the droplet graph; see :mod:`hydromemory.storage.sqlite_repository`.
"""
from __future__ import annotations

import sqlite3

# The promoted/queryable droplet columns plus the JSON sidecar columns.
_DROPLETS_DDL = """
CREATE TABLE IF NOT EXISTS droplets (
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
    external_sharing  INTEGER,
    purity            REAL,
    state_json        TEXT,
    permissions_json  TEXT,
    semantic_tags_json TEXT,
    cycle_json        TEXT,
    meta_json         TEXT,
    app_id            TEXT
)
"""

# The droplet graph (associations|contradictions|supports|derived_from edges).
_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS links (
    src_id TEXT NOT NULL,
    kind   TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    UNIQUE (src_id, kind, dst_id)
)
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_droplets_reservoir ON droplets (reservoir)",
    "CREATE INDEX IF NOT EXISTS idx_droplets_phase ON droplets (phase)",
    "CREATE INDEX IF NOT EXISTS idx_droplets_memory_type ON droplets (memory_type)",
    "CREATE INDEX IF NOT EXISTS idx_droplets_visibility ON droplets (visibility)",
    "CREATE INDEX IF NOT EXISTS idx_droplets_purity ON droplets (purity)",
    "CREATE INDEX IF NOT EXISTS idx_links_src ON links (src_id)",
    "CREATE INDEX IF NOT EXISTS idx_links_dst ON links (dst_id)",
    "CREATE INDEX IF NOT EXISTS idx_droplets_app_id ON droplets (app_id)",
)


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) a SQLite database at ``db_path``.

    Rows come back as :class:`sqlite3.Row` (name-addressable). Foreign keys are
    not declared on the schema, so ``check_same_thread`` defaults are fine; we
    only need single-connection usage in the reference implementation.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the ``droplets`` + ``links`` tables and indexes if absent.

    Additively migrates a pre-v2 database by adding the nullable ``app_id``
    column (used by the §9 vault for per-app scoping) when it is missing, so an
    existing v1 store opens unchanged.
    """
    conn.execute(_DROPLETS_DDL)
    conn.execute(_LINKS_DDL)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(droplets)").fetchall()}
    if "app_id" not in columns:
        conn.execute("ALTER TABLE droplets ADD COLUMN app_id TEXT")
    for stmt in _INDEX_DDL:
        conn.execute(stmt)
    conn.commit()
