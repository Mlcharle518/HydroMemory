"""open_store dispatch + Postgres-less error paths.

These tests run in every environment (no Postgres needed). They verify:

- `open_store` routes to the SQLite repo for the default config.
- `open_store` rejects an unknown backend.
- The Postgres repo raises a clear error when `database_url` is missing.
- The wizard's `.env` projection includes `HYDRO_DATABASE_URL` only when
  Postgres is selected.
"""
from __future__ import annotations

import pytest

from hydromemory.config import HydroConfig
from hydromemory.onboarding import InitAnswers, _env_pairs
from hydromemory.storage import SqliteDropletRepository, open_store


def test_open_store_default_is_sqlite(tmp_path):
    cfg = HydroConfig(db_path=str(tmp_path / "x.db"))
    store = open_store(cfg)
    try:
        assert isinstance(store, SqliteDropletRepository)
    finally:
        store.close()


def test_open_store_unknown_backend_errors(tmp_path):
    cfg = HydroConfig(db_path=str(tmp_path / "x.db"), storage_backend="redis")
    with pytest.raises(ValueError, match="unknown storage_backend"):
        open_store(cfg)


def test_postgres_repo_without_database_url_raises():
    # The lazy import is fine — the constructor checks `database_url` before
    # importing psycopg, so this runs even without the [postgres] extra installed.
    from hydromemory.storage.postgres_repository import PostgresDropletRepository

    cfg = HydroConfig(storage_backend="postgres", database_url=None)
    with pytest.raises(ValueError, match="requires database_url"):
        PostgresDropletRepository(cfg)


def test_env_pairs_omits_database_url_for_sqlite():
    answers = InitAnswers()  # defaults: storage_backend == "sqlite"
    keys = {k for k, _ in _env_pairs(answers)}
    assert "HYDRO_DATABASE_URL" not in keys
    assert "HYDRO_STORAGE_BACKEND" in keys


def test_env_pairs_includes_database_url_for_postgres():
    answers = InitAnswers(
        storage_backend="postgres",
        database_url="postgresql://localhost/hydromemory",
    )
    pairs = dict(_env_pairs(answers))
    assert pairs["HYDRO_STORAGE_BACKEND"] == "postgres"
    assert pairs["HYDRO_DATABASE_URL"] == "postgresql://localhost/hydromemory"
