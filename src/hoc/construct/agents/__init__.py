"""The agent team. Importing this package registers the built-in roles."""

from hoc.construct.agents import builtin as _builtin  # noqa: F401  (registers agents)
from hoc.construct.agents import materials as _materials  # noqa: F401
from hoc.construct.agents.base import (
    AGENTS,
    Agent,
    AgentResult,
    Proposal,
    Question,
    create_agent,
    register_agent,
)

__all__ = [
    "AGENTS",
    "Agent",
    "AgentResult",
    "Proposal",
    "Question",
    "create_agent",
    "register_agent",
]
