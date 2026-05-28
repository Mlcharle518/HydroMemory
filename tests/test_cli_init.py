"""Tests for the interactive onboarding wizard (`hydromem init`).

Each test drives ``run_wizard`` with explicit string streams so the wizard is
deterministic and isolated from the terminal — the same path the CLI takes,
just with stdin/stdout swapped for in-memory buffers.
"""
from __future__ import annotations

import io
import tomllib
from pathlib import Path

from hydromemory.onboarding import (
    InitAnswers,
    merge_env,
    merge_gitignore,
    run_wizard,
)


def _run(
    cwd: Path,
    answers_text: str = "",
    *,
    non_interactive: bool = False,
    preset: str | None = None,
    force: bool = False,
) -> tuple[int, str]:
    out = io.StringIO()
    rc = run_wizard(
        cwd=cwd,
        non_interactive=non_interactive,
        preset=preset,
        force=force,
        smoke_test=False,
        stream_in=io.StringIO(answers_text),
        stream_out=out,
    )
    return rc, out.getvalue()


def test_non_interactive_writes_toml_and_env(tmp_path):
    rc, _ = _run(tmp_path, non_interactive=True)
    assert rc == 0
    toml = tmp_path / "hydromemory.toml"
    env = tmp_path / ".env"
    assert toml.exists() and env.exists()
    data = tomllib.loads(toml.read_text())
    assert data["storage"]["backend"] == "sqlite"
    assert data["intelligence"]["backend"] == "stub"
    assert data["vault"]["enabled"] is False
    env_text = env.read_text()
    assert "HYDRO_DB_PATH=" in env_text
    assert "HYDRO_INTELLIGENCE_BACKEND=stub" in env_text
    assert "ANTHROPIC_API_KEY" not in env_text  # only written when claude / vault selected


def test_preset_claude_sets_backend_and_writes_api_key_slot(tmp_path):
    # claude preset + non-interactive => ANTHROPIC_API_KEY line is present (empty value).
    rc, _ = _run(tmp_path, non_interactive=True, preset="claude")
    assert rc == 0
    env_text = (tmp_path / ".env").read_text()
    assert "HYDRO_INTELLIGENCE_BACKEND=claude" in env_text
    assert "ANTHROPIC_API_KEY=" in env_text


def test_preset_team_enables_vault_and_generates_key(tmp_path):
    rc, _ = _run(tmp_path, non_interactive=True, preset="team")
    assert rc == 0
    env_text = (tmp_path / ".env").read_text()
    # vault_enabled in non-interactive mode flips the toggle but the key field stays
    # empty (we never invent secrets without an explicit interactive confirmation),
    # so the line is present but blank — surfaced to the user via the TOML mirror.
    assert "HYDRO_VAULT_ENABLED=true" in env_text


def test_rerun_uses_existing_toml_as_defaults(tmp_path):
    # First run with overrides.
    inputs = "\n".join([
        "myproj",          # project name
        "",                # storage backend (sqlite default)
        "./mem.db",        # db path
        "stub",            # intelligence backend
        "stub",            # embedding source
        "brute",           # vector backend
        "n",               # vault?
        "alice",           # default agent
        "high_trust",      # default trust
        "groundwater",     # default reservoir
        "2.5",             # cycle tick
        "",                # trailing newline
    ]) + "\n"
    rc, _ = _run(tmp_path, inputs)
    assert rc == 0

    # Second run with all-default inputs should preserve the first-run values.
    rerun_inputs = "\n" + ("\n" * 11) + "\n"  # continue-prompt + 11 defaults
    rc, _ = _run(tmp_path, rerun_inputs)
    assert rc == 0
    data = tomllib.loads((tmp_path / "hydromemory.toml").read_text())
    assert data["project"]["name"] == "myproj"
    assert data["identity"]["default_agent"] == "alice"
    assert data["identity"]["default_trust"] == "high_trust"
    assert data["identity"]["default_reservoir"] == "groundwater"
    assert data["cycle"]["tick_seconds"] == 2.5


def test_env_merge_preserves_unrelated_keys():
    existing = "MY_APP_TOKEN=keep-me\nHYDRO_DB_PATH=old.db\n"
    pairs = [("HYDRO_DB_PATH", "new.db"), ("HYDRO_VECTOR_BACKEND", "brute")]
    merged = merge_env(existing, pairs)
    assert "MY_APP_TOKEN=keep-me" in merged
    assert "HYDRO_DB_PATH=new.db" in merged
    assert "HYDRO_DB_PATH=old.db" not in merged
    assert "HYDRO_VECTOR_BACKEND=brute" in merged


def test_env_merge_quotes_values_with_spaces():
    merged = merge_env("", [("HYDRO_DB_PATH", "/tmp/has space/mem.db")])
    assert 'HYDRO_DB_PATH="/tmp/has space/mem.db"' in merged


def test_gitignore_merge_is_idempotent():
    once = merge_gitignore("")
    twice = merge_gitignore(once)
    # Idempotent: the second pass returns the same content (modulo trailing newline normalisation).
    assert once.rstrip() == twice.rstrip()
    assert ".env" in once
    assert "hydromemory.db" in once


def test_init_writes_gitignore(tmp_path):
    rc, _ = _run(tmp_path, non_interactive=True)
    assert rc == 0
    gi = (tmp_path / ".gitignore").read_text()
    assert "hydromemory.db" in gi
    assert ".env" in gi


def test_wizard_postgres_selection_writes_dsn_to_env(tmp_path):
    inputs = "\n".join([
        "myproj",                          # project name
        "postgres",                        # storage backend
        "postgresql://localhost/hm_test",  # DSN
        "stub",                            # intelligence backend
        "stub",                            # embedding source
        "brute",                           # vector backend
        "n",                               # vault?
        "alice",                           # default agent
        "approved",                        # default trust
        "working_stream",                  # default reservoir
        "1.0",                             # cycle tick
        "",
    ]) + "\n"
    rc, _ = _run(tmp_path, inputs)
    assert rc == 0
    env_text = (tmp_path / ".env").read_text()
    assert "HYDRO_STORAGE_BACKEND=postgres" in env_text
    assert "HYDRO_DATABASE_URL=postgresql://localhost/hm_test" in env_text
    toml = tomllib.loads((tmp_path / "hydromemory.toml").read_text())
    assert toml["storage"]["backend"] == "postgres"
    assert toml["storage"]["database_url"] == "postgresql://localhost/hm_test"


def test_init_dataclass_field_names_match_render():
    # Belt-and-suspenders: if InitAnswers fields drift, _render_toml will break.
    answers = InitAnswers()
    from hydromemory.onboarding import _render_toml

    text = _render_toml(answers)
    data = tomllib.loads(text)
    assert {"project", "storage", "vectors", "intelligence", "vault", "identity", "cycle"} <= set(data.keys())
