"""HydroCognitive developer SDK (Master Spec §22 MVP-7 / §25 "HydroCognitive SDK").

A thin, ergonomic surface over a fully-wired :class:`~hydromemory.engine.Engine`:
:class:`HydroClient` exposes the §18 canonical protocol verbs uniformly, projects
and validates layer objects against the canonical §8 JSON Schemas, and bridges the
unified cognitive bus (§17) — so an external developer drives the whole 9-layer
stack through one object. :class:`SdkError` is the single error type the surface
raises (e.g. when a verb's layer is disabled).

The SDK is dependency-light and additive: it reuses the canonical/engine/bus
modules and renames nothing (ADR-0048).
"""
from __future__ import annotations

from hydromemory.sdk.client import HydroClient, SdkError

__all__ = ["HydroClient", "SdkError"]
