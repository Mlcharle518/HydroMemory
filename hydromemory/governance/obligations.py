"""Governance value types: operations, obligations, and access decisions (PRD §10).

Obligations are *returned* by ``check_access``, not auto-applied — the caller
(engine/verb) is responsible for satisfying them (e.g. obtaining consent, running
a thaw protocol, attaching an explanation) before proceeding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Operation(str, Enum):
    READ = "read"
    EXPOSE_TO_USER = "expose_to_user"
    MUTATE = "mutate"
    TRANSFORM = "transform"
    OVERWRITE = "overwrite"
    USE_FOR_GENERATION = "use_for_generation"


class Obligation(str, Enum):
    REQUIRES_EXPLANATION = "requires_explanation"
    REQUIRES_THAW = "requires_thaw"
    REQUIRES_CONSENT = "requires_consent"
    OVERWRITE_BLOCKED = "overwrite_blocked"


@dataclass
class AccessDecision:
    allowed: bool
    denial_reason: str | None = None
    obligations: list[Obligation] = field(default_factory=list)
    usable_for_generation: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "denial_reason": self.denial_reason,
            "obligations": [o.value for o in self.obligations],
            "usable_for_generation": self.usable_for_generation,
        }
