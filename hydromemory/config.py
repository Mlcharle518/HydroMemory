"""Runtime configuration: storage path, vector dimension, and the pluggable
intelligence backend selection (PRD: stub default, optional Claude backend)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class HydroConfig:
    db_path: str = "hydromemory.db"
    # Storage backend: "sqlite" (default, file-backed) | "postgres" (pgvector).
    storage_backend: str = "sqlite"
    # Postgres DSN; required when storage_backend == "postgres". Read from
    # HYDRO_DATABASE_URL so secrets stay out of argv.
    database_url: str | None = None
    vector_dim: int = 256
    vector_backend: str = "brute"  # "brute" (exact, default) | "ann" (hnswlib, .[ann] extra)
    intelligence_backend: str = "stub"  # "stub" | "claude"
    embedding_backend: str = "stub"  # "stub" | "claude"
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-4-7"  # text-ops model when intelligence_backend="claude"
    # v2 §9 User-Controlled Memory Vault (all default off; v1 behavior unchanged).
    vault_enabled: bool = False
    vault_key: str | None = None
    # Retired vault keys kept for decryption during/after a key rotation: the
    # primary (``vault_key``) encrypts new writes; these still decrypt old rows
    # until ``VaultRepository.rotate_keys`` re-encrypts everything to the primary.
    vault_prev_keys: list[str] = field(default_factory=list)
    app_id: str | None = None
    # HydroIntent layer (additive, default off; ADR-0025/0037). When False the
    # engine is byte-identical to the memory-only build.
    intents_enabled: bool = False
    # HydroJudgment layer (additive, default off; ADR-0043). Consumes Intent objects.
    judgment_enabled: bool = False
    # HydroPlan layer (additive, default off; ADR-0044). Consumes Intent + Judgment objects.
    planning_enabled: bool = False
    # HydroAction layer (additive, default off; ADR-0045). Consumes Plan/Judgment objects.
    action_enabled: bool = False
    # HydroReflect layer (additive, default off; ADR-0046). Consumes Action outcomes.
    reflect_enabled: bool = False
    # HydroIntegrate layer (additive, default off; ADR-0050). Consumes Reflect's
    # recommended_updates and commits governed updates back into the stack (the loop-closer).
    integrate_enabled: bool = False
    # HydroSense layer (additive, default off; ADR-0051). Stack position 1: perception intake —
    # turns signals/user input/app events into observation events for HydroMemory.
    sense_enabled: bool = False
    # HydroIdentity layer (additive, default off; ADR-0052). Stack position 3: the stable-pattern
    # layer between Memory and Intent (roles/values/boundaries/posture); highest-caution updates (§16).
    identity_enabled: bool = False
    # Wizard-seeded defaults consumed by the CLI when no explicit flag is passed.
    default_agent: str = "assistant"
    default_trust: str = "approved"
    default_reservoir: str = "working_stream"
    cycle_tick_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> HydroConfig:
        backend = os.environ.get("HYDRO_INTELLIGENCE_BACKEND", "stub")
        embedding_backend = os.environ.get("HYDRO_EMBEDDING_BACKEND", backend)
        # The local embedder is 384-dim; default the store to match when selected.
        default_dim = "384" if embedding_backend.lower() == "local" else "256"
        return cls(
            db_path=os.environ.get("HYDRO_DB_PATH", "hydromemory.db"),
            storage_backend=os.environ.get("HYDRO_STORAGE_BACKEND", "sqlite"),
            database_url=os.environ.get("HYDRO_DATABASE_URL"),
            vector_dim=int(os.environ.get("HYDRO_EMBED_DIM", default_dim)),
            vector_backend=os.environ.get("HYDRO_VECTOR_BACKEND", "brute"),
            intelligence_backend=backend,
            embedding_backend=embedding_backend,
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            claude_model=os.environ.get("HYDRO_CLAUDE_MODEL", "claude-opus-4-7"),
            vault_enabled=os.environ.get("HYDRO_VAULT_ENABLED", "").lower() in ("1", "true", "yes"),
            vault_key=os.environ.get("HYDRO_VAULT_KEY"),
            vault_prev_keys=[
                k.strip() for k in os.environ.get("HYDRO_VAULT_PREV_KEYS", "").split(",") if k.strip()
            ],
            app_id=os.environ.get("HYDRO_APP_ID"),
            intents_enabled=os.environ.get("HYDRO_INTENTS_ENABLED", "").lower() in ("1", "true", "yes"),
            judgment_enabled=os.environ.get("HYDRO_JUDGMENT_ENABLED", "").lower() in ("1", "true", "yes"),
            planning_enabled=os.environ.get("HYDRO_PLANNING_ENABLED", "").lower() in ("1", "true", "yes"),
            action_enabled=os.environ.get("HYDRO_ACTION_ENABLED", "").lower() in ("1", "true", "yes"),
            reflect_enabled=os.environ.get("HYDRO_REFLECT_ENABLED", "").lower() in ("1", "true", "yes"),
            integrate_enabled=os.environ.get("HYDRO_INTEGRATE_ENABLED", "").lower() in ("1", "true", "yes"),
            sense_enabled=os.environ.get("HYDRO_SENSE_ENABLED", "").lower() in ("1", "true", "yes"),
            identity_enabled=os.environ.get("HYDRO_IDENTITY_ENABLED", "").lower() in ("1", "true", "yes"),
            default_agent=os.environ.get("HYDRO_DEFAULT_AGENT", "assistant"),
            default_trust=os.environ.get("HYDRO_DEFAULT_TRUST", "approved"),
            default_reservoir=os.environ.get("HYDRO_DEFAULT_RESERVOIR", "working_stream"),
            cycle_tick_seconds=float(os.environ.get("HYDRO_CYCLE_TICK_SECONDS", "1.0")),
        )
