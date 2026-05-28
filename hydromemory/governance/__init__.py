"""Governance: reservoir-access policy + the single ``check_access`` entry point.

Exports the §10 enforcement surface that Track B (lifecycle/recall) consumes:
``check_access`` plus the continuous ``permission_score`` / ``privacy_risk``
companions, the declarative ``ReservoirRule`` / ``rule_for`` policy, and the
frozen value types (decision, obligation, operation, agent/context, trust).
"""
from hydromemory.governance.enforcement import (
    AccessContext,
    AgentIdentity,
    TrustLevel,
    check_access,
)
from hydromemory.governance.obligations import AccessDecision, Obligation, Operation
from hydromemory.governance.policy import (
    AccessLevel,
    ReservoirRule,
    all_rules,
    rule_for,
)
from hydromemory.governance.scoring import (
    permission_score,
    privacy_risk,
    reservoir_sensitivity,
)

__all__ = [
    "check_access",
    "permission_score",
    "privacy_risk",
    "reservoir_sensitivity",
    "rule_for",
    "all_rules",
    "ReservoirRule",
    "AccessLevel",
    "AccessDecision",
    "Obligation",
    "Operation",
    "AgentIdentity",
    "AccessContext",
    "TrustLevel",
]
