"""Shared test fixtures.

The spec's own JSON blobs (PRD §5.2, §6, §12 Example A/F, §10.1) are the
ground-truth data the contracts must admit. Prose-only §12 examples (B/C/D/E)
become scenario assertions in Phase 3, not JSON round-trips.
"""
from __future__ import annotations

from typing import Any

import pytest

# --- PRD §5.2: droplet encoding example -------------------------------------
DROPLET_5_2: dict[str, Any] = {
    "memory_id": "mem_9f31",
    "type": "conceptual_preference",
    "content": "User wants AI memory to preserve thinking style, not just factual details.",
    "phase": "liquid",
    "reservoir": "working_stream",
    "semantic_tags": ["AI memory", "thinking style", "personalization", "architecture"],
    "state": {
        "temperature": 0.72,
        "pressure": 0.64,
        "salinity": 0.18,
        "purity": 0.91,
        "depth": 0.22,
        "fluidity": 0.86,
        "integrity": 0.94,
        "emotional_charge": 0.58,
        "confidence": 0.89,
    },
    "permissions": {
        "scope": "user_private",
        "agent_access": ["personal_assistant", "reasoning_agent"],
        "retention": "persistent",
        "requires_consent_for_external_use": True,
    },
}

# --- PRD §6: machine-readable protocol envelope (ABSORB) --------------------
ENVELOPE_6: dict[str, Any] = {
    "protocol": "HydroMemory",
    "version": "1.0",
    "operation": "ABSORB",
    "input": {
        "content": "User prefers architectural systems thinking over shallow summaries.",
        "source": "conversation",
        "timestamp": "2026-05-25T14:22:00Z",
        "context": {"topic": "AI memory systems", "session_type": "design"},
    },
    "classification": {
        "memory_type": "cognitive_style",
        "importance": 0.86,
        "sensitivity": 0.41,
        "expected_lifespan": "persistent",
    },
    "initial_state": {
        "phase": "liquid",
        "reservoir": "surface_reservoir",
        "temperature": 0.74,
        "pressure": 0.68,
        "gravity": 0.81,
        "purity": 0.93,
        "fluidity": 0.88,
        "depth": 0.24,
    },
    "permissions": {
        "owner": "user",
        "visibility": "private",
        "allowed_agents": ["assistant", "planning_agent", "reasoning_agent"],
        "external_sharing": False,
        "requires_user_review": False,
    },
}

# --- PRD §12 Example A: initial droplet (loose shape) -----------------------
EXAMPLE_A_DROPLET: dict[str, Any] = {
    "content": "I was dismissed during a meeting.",
    "context": ["work", "authority", "public speaking"],
    "charge": 0.68,
    "pressure": 0.55,
    "phase": "liquid",
}

# --- PRD §12 Example F: conflict-resolution updated_memory ------------------
EXAMPLE_F_UPDATED: dict[str, Any] = {
    "content": "User often prefers depth for architecture topics, but may prefer concise answers for simple tasks.",
    "phase": "filtered",
    "purity": 0.92,
}

# --- PRD §10.1: contamination output ----------------------------------------
CONTAMINATION_10_1: dict[str, Any] = {
    "memory_id": "mem_7712",
    "phase": "polluted",
    "reservoir": "contaminated_pool",
    "reason": "Low confidence inference from emotionally charged conversation.",
    "usable_for_generation": False,
    "requires_filtering": True,
}

# Droplet-shaped blobs (the envelope §6 is handled separately).
SPEC_DROPLET_BLOBS: dict[str, dict[str, Any]] = {
    "droplet_5_2": DROPLET_5_2,
    "example_a": EXAMPLE_A_DROPLET,
    "example_f": EXAMPLE_F_UPDATED,
    "contamination_10_1": CONTAMINATION_10_1,
}


@pytest.fixture
def spec_droplet_blobs() -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in SPEC_DROPLET_BLOBS.items()}


@pytest.fixture
def envelope_blob() -> dict[str, Any]:
    return dict(ENVELOPE_6)


@pytest.fixture
def tmp_db_path(tmp_path) -> str:
    return str(tmp_path / "hydro.db")


@pytest.fixture
def config(tmp_db_path):
    from hydromemory.config import HydroConfig

    return HydroConfig(db_path=tmp_db_path, vector_dim=64, intelligence_backend="stub")


@pytest.fixture
def stub_intelligence(config):
    """Available to Phase 1+ tests; builds the offline stub intelligence bundle."""
    from hydromemory.intelligence import build_intelligence

    return build_intelligence(config)
