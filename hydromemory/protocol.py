"""Machine-readable protocol envelope (PRD §6).

Verb requests/responses are expressed as :class:`ProtocolEnvelope` /
:class:`ProtocolResponse`. The ``input``, ``classification``, ``initial_state``
and ``permissions`` blocks are kept as plain dicts so every key the spec emits
round-trips losslessly; typed views (e.g. the classifier output) live in
``hydromemory.intelligence``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_NAME = "HydroMemory"
PROTOCOL_VERSION = "1.0"


@dataclass
class ProtocolEnvelope:
    operation: str
    protocol: str = PROTOCOL_NAME
    version: str = PROTOCOL_VERSION
    input: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    initial_state: dict[str, Any] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "operation": self.operation,
            "input": dict(self.input),
            "classification": dict(self.classification),
            "initial_state": dict(self.initial_state),
            "permissions": dict(self.permissions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProtocolEnvelope:
        data = dict(data)
        return cls(
            operation=str(data["operation"]),
            protocol=str(data.get("protocol", PROTOCOL_NAME)),
            version=str(data.get("version", PROTOCOL_VERSION)),
            input=dict(data.get("input") or {}),
            classification=dict(data.get("classification") or {}),
            initial_state=dict(data.get("initial_state") or {}),
            permissions=dict(data.get("permissions") or {}),
        )


@dataclass
class ProtocolResponse:
    operation: str
    protocol: str = PROTOCOL_NAME
    version: str = PROTOCOL_VERSION
    result: Any = None
    decision: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "operation": self.operation,
            "result": self.result,
            "decision": self.decision,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProtocolResponse:
        data = dict(data)
        return cls(
            operation=str(data["operation"]),
            protocol=str(data.get("protocol", PROTOCOL_NAME)),
            version=str(data.get("version", PROTOCOL_VERSION)),
            result=data.get("result"),
            decision=data.get("decision"),
            outcome=data.get("outcome"),
        )
