"""Agent roles (PRD §8) as synchronous library modules + the runtime that ticks
them. §9 (the OS-level publish/subscribe event bus and integration levels L1-L4)
is deferred; :class:`AgentRuntime` is the seam where it will later slot in.
"""
from hydromemory.agents.archivist import ArchivistAgent
from hydromemory.agents.base import Agent, AgentContext, BaseAgent
from hydromemory.agents.capture import CaptureAgent
from hydromemory.agents.distillation import DistillationAgent
from hydromemory.agents.filtration import FiltrationAgent
from hydromemory.agents.hydrologist import HydrologistAgent
from hydromemory.agents.privacy import PrivacyAgent
from hydromemory.agents.recall_agent import RecallAgent
from hydromemory.agents.reflection import ReflectionAgent
from hydromemory.agents.registry import AgentRuntime, build_default_runtime

__all__ = [
    "Agent",
    "AgentContext",
    "BaseAgent",
    "AgentRuntime",
    "build_default_runtime",
    "CaptureAgent",
    "HydrologistAgent",
    "ArchivistAgent",
    "FiltrationAgent",
    "RecallAgent",
    "PrivacyAgent",
    "ReflectionAgent",
    "DistillationAgent",
]
