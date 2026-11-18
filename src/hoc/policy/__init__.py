"""Pedagogical policy: what could happen next, from beliefs and graph structure. No LLM."""

from hoc.policy import heuristic as _heuristic  # noqa: F401  (registers policies)
from hoc.policy.base import (
    POLICIES,
    Policy,
    PolicyContext,
    TaskSource,
    create_policy,
    register_policy,
)
from hoc.policy.frontier import (
    dependents,
    essential_prerequisites,
    frontier,
    prerequisite_in_place,
    weakness,
)
from hoc.policy.path import learning_path

__all__ = [
    "POLICIES",
    "Policy",
    "PolicyContext",
    "TaskSource",
    "create_policy",
    "dependents",
    "essential_prerequisites",
    "frontier",
    "learning_path",
    "prerequisite_in_place",
    "register_policy",
    "weakness",
]
