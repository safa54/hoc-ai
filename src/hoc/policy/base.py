"""Policy contract (ARCHITECTURE §9.2): given beliefs and the graph, propose scored moves.

Pure and deterministic; never calls an LLM. The tutor may only choose among the moves a policy
offers, and every offered set is logged as a ``PolicyDecision`` (invariant L1.3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from hoc.config import LearnSettings
from hoc.core.assessment import Task
from hoc.core.errors import HocError
from hoc.core.learning import LearnerState, Move
from hoc.store.knowledge_graph import KnowledgeGraph


class TaskSource(Protocol):
    """What the policy needs from the task bank (see hoc.assess.TaskBank)."""

    def for_concept(
        self, concept_id: str, *, exclude_ids: set[str] | None = None, limit: int = 3
    ) -> list[Task]: ...


@dataclass
class PolicyContext:
    state: LearnerState
    graph: KnowledgeGraph
    settings: LearnSettings
    focus: str | None = None
    bank: TaskSource | None = None
    recent_moves: list[tuple[str, str | None]] = field(default_factory=list)  # (kind, target)
    seen_task_ids: set[str] = field(default_factory=set)
    goal: str | None = None  # concept the learner is working towards (None: the whole topic)
    area_order: list[str] = field(default_factory=list)  # planner order of the areas


class Policy(Protocol):
    id: str

    def candidates(self, ctx: PolicyContext) -> list[Move]:
        """All candidate moves, best first. Always ends with a ``stay`` baseline."""
        ...


PolicyFactory = Callable[[], Policy]
POLICIES: dict[str, PolicyFactory] = {}


def register_policy[F: PolicyFactory](name: str) -> Callable[[F], F]:
    def decorator(factory: F) -> F:
        POLICIES[name] = factory
        return factory

    return decorator


def create_policy(name: str) -> Policy:
    try:
        return POLICIES[name]()
    except KeyError:
        raise HocError(f"no policy registered as {name!r}; known: {sorted(POLICIES)}") from None
