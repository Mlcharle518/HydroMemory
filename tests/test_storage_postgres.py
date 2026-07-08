"""Postgres + pgvector droplet-store tests.

Real backend, real Postgres. CI does not provision Postgres, so the entire
module is **skipped** unless ``HYDRO_TEST_POSTGRES_DSN`` is set in the
environment. Local devs / future CI lanes with a Postgres + pgvector sidecar
can run these via:

    HYDRO_TEST_POSTGRES_DSN=postgresql://localhost/hydromemory_test \\
        pytest tests/test_storage_postgres.py

Each test allocates a unique schema and drops it on teardown so the suite is
safe to re-run against a single database.
"""
from __future__ import annotations

import os
import uuid

import pytest

from hydromemory.config import HydroConfig
from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, Visibility

DSN = os.environ.get("HYDRO_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="HYDRO_TEST_POSTGRES_DSN not set — skipping Postgres backend tests",
)


@pytest.fixture
def repo():
    """Yield a fresh PostgresDropletRepository on an isolated schema."""
    from hydromemory.storage.postgres_repository import PostgresDropletRepository

    schema = f"hm_test_{uuid.uuid4().hex[:8]}"
    # Connect once with a search_path that points at the new schema; the repo's
    # CREATE TABLE statements will land there.
    dsn_with_schema = f"{DSN}?options=-csearch_path%3D{schema}"
    cfg = HydroConfig(
        storage_backend="postgres",
        database_url=dsn_with_schema,
        vector_dim=8,  # small so test vectors stay readable
    )
    # Bootstrap: create the schema before the repo opens its first cursor.
    import psycopg  # type: ignore[import-untyped]

    with psycopg.connect(DSN, autocommit=True) as boot:
        with boot.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    repo = PostgresDropletRepository(cfg)
    try:
        yield repo
    finally:
        repo.close()
        with psycopg.connect(DSN, autocommit=True) as boot:
            with boot.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _seed(repo, *, did: str, content: str, reservoir=Reservoir.WORKING_STREAM, embedding=None):
    droplet = Droplet.from_dict(
        {
            "id": did,
            "content": content,
            "reservoir": reservoir.value,
            "state": {"purity": 0.9},
            "embedding": embedding,
        }
    )
    repo.upsert(droplet)
    return droplet


def test_upsert_get_roundtrip(repo):
    _seed(repo, did="m1", content="hello postgres")
    fetched = repo.get("m1")
    assert fetched is not None
    assert fetched.content == "hello postgres"
    assert fetched.reservoir is Reservoir.WORKING_STREAM


def test_all_ids_preserves_insertion_order(repo):
    for i, did in enumerate(["a", "b", "c", "d"]):
        _seed(repo, did=did, content=f"d-{i}")
    assert repo.all_ids() == ["a", "b", "c", "d"]


def test_delete_removes_row_and_links(repo):
    _seed(repo, did="m1", content="one")
    _seed(repo, did="m2", content="two")
    repo.add_link("m1", "associations", "m2")
    repo.delete("m1")
    assert repo.get("m1") is None
    # The associations link to m1 should also be gone (no orphan link rows).
    m2 = repo.get("m2")
    assert m2 is not None
    assert "m1" not in m2.links.associations


def test_query_filters(repo):
    _seed(repo, did="m1", content="ws", reservoir=Reservoir.WORKING_STREAM)
    _seed(repo, did="m2", content="gw", reservoir=Reservoir.GROUNDWATER)
    only_ws = repo.query(reservoir=Reservoir.WORKING_STREAM)
    assert [d.id for d in only_ws] == ["m1"]
    none_polluted = repo.query(phase=Phase.POLLUTED)
    assert none_polluted == []


def test_search_similar_returns_nearest(repo):
    _seed(repo, did="m1", content="near", embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    _seed(repo, did="m2", content="far", embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    hits = repo.search_similar([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], k=2)
    assert hits[0][0] == "m1"
    # pgvector returns cosine *distance*; the repo converts to similarity.
    assert hits[0][1] > hits[1][1]


def test_links_roundtrip(repo):
    _seed(repo, did="m1", content="a")
    _seed(repo, did="m2", content="b")
    repo.add_link("m1", "associations", "m2")
    repo.add_link("m1", "supports", "m2")
    m1 = repo.get("m1")
    assert "m2" in m1.links.associations
    assert "m2" in m1.links.supports
    repo.remove_link("m1", "associations", "m2")
    m1_again = repo.get("m1")
    assert "m2" not in m1_again.links.associations
    assert "m2" in m1_again.links.supports


def test_touch_cycle_updates_fields(repo):
    from datetime import UTC, datetime

    _seed(repo, did="m1", content="touched")
    now = datetime.now(UTC)
    repo.touch_cycle("m1", recalled=now, increment_count=True)
    refreshed = repo.get("m1")
    assert refreshed.cycle.cycle_count == 1
    assert refreshed.cycle.last_recalled is not None


def test_query_allowed_agent_public_user_visibility(repo):
    droplet = Droplet.from_dict(
        {
            "id": "pub",
            "content": "shareable",
            "permissions": {"owner": "user", "visibility": Visibility.PUBLIC.value},
        }
    )
    repo.upsert(droplet)
    assert [d.id for d in repo.query(allowed_agent="random-agent")] == ["pub"]
