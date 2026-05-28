"""Declarative reservoir-access policy loader (PRD §10).

The policy is data, not code: ``policy_data.json`` reproduces the §10
``reservoir_access`` block verbatim, and this module loads it into frozen
:class:`ReservoirRule` dataclasses keyed by :class:`Reservoir`.

Access levels (the ``access`` strings in §10) map onto the minimum
:class:`~hydromemory.governance.enforcement.TrustLevel` an agent must hold, plus
two special gates that the enforcement layer interprets (consent/thaw for
``glacier`` and ``sacred``; filtration-only for ``contaminated``). The §10 JSON
only enumerates six reservoirs; ``cloud`` and ``ocean`` are not in the spec block
so we give them sensible, documented defaults (``cloud`` behaves like an
abstracted ``surface`` layer -> approved agents; ``ocean`` is the collective
privacy-bounded layer -> high-trust only). Any flag a rule omits inherits the
documented defaults: ``user_visible=False``, ``requires_explanation=False``,
``requires_thaw_protocol=False``, ``usable_for_response=True``,
``overwrite_allowed=True``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from hydromemory.reservoirs import Reservoir, normalize_reservoir

_POLICY_PATH = Path(__file__).with_name("policy_data.json")


class AccessLevel(str, Enum):
    """The §10 ``access`` strings, normalized to a stable enum.

    These describe *who* may access a reservoir at all. The enforcement layer
    further refines them via trust level and the consent/thaw/filtration gates.
    """

    SESSION_AGENTS = "session_agents"
    APPROVED_AGENTS = "approved_agents"
    HIGH_TRUST_AGENTS_ONLY = "high_trust_agents_only"
    EXPLICIT_USER_CONSENT = "explicit_user_consent"
    FILTRATION_AGENT_ONLY = "filtration_agent_only"
    EXPLICIT_USER_CONSENT_OR_USER_DEFINED_CORE_BEHAVIOR = (
        "explicit_user_consent_or_user_defined_core_behavior"
    )


# Documented defaults for any flag a §10 rule omits.
_DEFAULTS: dict[str, Any] = {
    "user_visible": False,
    "requires_explanation": False,
    "requires_thaw_protocol": False,
    "usable_for_response": True,
    "overwrite_allowed": True,
}


@dataclass(frozen=True)
class ReservoirRule:
    """The resolved access rule for a single reservoir (PRD §10)."""

    reservoir: Reservoir
    access_level: AccessLevel
    user_visible: bool = False
    requires_explanation: bool = False
    requires_thaw_protocol: bool = False
    usable_for_response: bool = True
    overwrite_allowed: bool = True


def _build_rules() -> dict[Reservoir, ReservoirRule]:
    data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    rules: dict[Reservoir, ReservoirRule] = {}
    for entry in data.get("rules", []):
        reservoir = normalize_reservoir(entry["reservoir"])
        rules[reservoir] = ReservoirRule(
            reservoir=reservoir,
            access_level=AccessLevel(entry["access"]),
            user_visible=bool(entry.get("user_visible", _DEFAULTS["user_visible"])),
            requires_explanation=bool(
                entry.get("requires_explanation", _DEFAULTS["requires_explanation"])
            ),
            requires_thaw_protocol=bool(
                entry.get("requires_thaw_protocol", _DEFAULTS["requires_thaw_protocol"])
            ),
            usable_for_response=bool(
                entry.get("usable_for_response", _DEFAULTS["usable_for_response"])
            ),
            overwrite_allowed=bool(
                entry.get("overwrite_allowed", _DEFAULTS["overwrite_allowed"])
            ),
        )
    return rules


@lru_cache(maxsize=1)
def _rules() -> dict[Reservoir, ReservoirRule]:
    return _build_rules()


def rule_for(reservoir: Reservoir | str) -> ReservoirRule:
    """Return the resolved :class:`ReservoirRule` for ``reservoir``.

    Falls back to a conservative default rule (approved-agents, not user
    visible, not usable for response) for any reservoir absent from the policy,
    so an unknown reservoir fails closed rather than open.
    """
    res = normalize_reservoir(reservoir)
    rules = _rules()
    rule = rules.get(res)
    if rule is None:
        return ReservoirRule(
            reservoir=res,
            access_level=AccessLevel.APPROVED_AGENTS,
            usable_for_response=False,
        )
    return rule


def all_rules() -> dict[Reservoir, ReservoirRule]:
    """Return a copy of the full reservoir -> rule mapping."""
    return dict(_rules())
