"""HydroMemory platform layer (PRD §9): L1 apps, L3 mesh, L4 grants (v2).

Phase A0 ships the contracts; Phase B1 implements the mesh coordination, the
grant store + enforcement, and the app-memory handle.
"""
from hydromemory.platform.apps import AppMemory, register_app
from hydromemory.platform.grants import (
    GRANTS_DDL,
    Grant,
    GrantRequest,
    GrantStatus,
    GrantStore,
    enforce_grant,
)
from hydromemory.platform.mesh import Mesh
from hydromemory.platform.runtime import MeshEngine, build_app_views, build_mesh

__all__ = [
    "AppMemory",
    "register_app",
    "Mesh",
    "MeshEngine",
    "build_mesh",
    "build_app_views",
    "Grant",
    "GrantRequest",
    "GrantStatus",
    "GrantStore",
    "enforce_grant",
    "GRANTS_DDL",
]
