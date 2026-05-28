"""App identity + scope for the integration levels (v2).

L1 (App Memory): a scoped view bound to one ``app_id`` — an app sees only its own
droplets. L2 (User Memory Vault): the owner's cross-app view (``cross_app=True``)
that aggregates memory across apps, still gated by governance.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppIdentity:
    """An application registered against a user's memory layer."""

    app_id: str
    owner: str = "user"


@dataclass(frozen=True)
class AppScope:
    """The slice of the vault a request operates over.

    * L1 app scope: ``AppScope(app_id="calendar")`` — only that app's droplets.
    * L2 owner vault: ``AppScope(cross_app=True)`` — across all app scopes.
    """

    app_id: str | None = None
    cross_app: bool = False
