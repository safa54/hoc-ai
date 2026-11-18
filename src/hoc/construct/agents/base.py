"""Agent contract and registry.

An agent is any object with a ``role``, a ``name``/``version`` and ``run(task, ctx)``. It
returns an ``AgentResult``: proposals (draft ops, never applied by the agent), feedback
notes, questions for the expert, tests and traces. The engine decides what happens to them.

To add or replace an agent: subclass ``LLMAgent`` (or write any class matching ``Agent``),
decorate it with ``@register_agent("name")`` and point a role at it in ``agents.team``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from hoc.construct.context import BuildContext
from hoc.construct.drafts import DraftOp
from hoc.core.assessment import Task
from hoc.core.build import AgentTask, Blueprint, Feedback, GraphTest, TestTrace
from hoc.core.common import Actor
from hoc.core.errors import HocError


@dataclass
class Proposal:
    drafts: list[DraftOp]
    rationale: str
    confidence: float = 0.7
    addresses: list[str] = field(default_factory=list)  # feedback ids it resolves
    llm_call_id: str | None = None
    topic_id: str | None = None  # area that new concepts belong to (navigation only)


@dataclass
class Question:
    text: str
    options: list[str] = field(default_factory=list)
    target_kind: str = "graph"
    target_id: str | None = None


@dataclass
class AgentResult:
    summary: str = ""
    proposals: list[Proposal] = field(default_factory=list)
    feedback: list[Feedback] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    dismissals: list[tuple[str, str]] = field(default_factory=list)  # (feedback id, reason)
    blueprint: Blueprint | None = None
    tests: list[GraphTest] = field(default_factory=list)
    traces: list[TestTrace] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)  # extracted or matched exercises
    reviewed: list[str] | None = None  # critic: concepts whose cards it looked at (None: all)


class Agent(Protocol):
    role: str
    name: str
    version: str

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult: ...


class QuestionDraft(BaseModel):
    """Shared output fragment: a question an agent wants to put to the expert."""

    text: str
    options: list[str] = Field(default_factory=list)


class LLMAgent:
    """Convenience base: identity plus the actor used as ChangeSet/feedback author."""

    role = ""
    name = ""
    version = "1"

    @property
    def actor(self) -> Actor:
        return Actor(kind="agent", name=self.name, version=self.version)

    def questions(self, drafts: list[QuestionDraft]) -> list[Question]:
        return [Question(q.text.strip(), q.options) for q in drafts if q.text.strip()]


AgentFactory = Callable[[], Agent]
AGENTS: dict[str, AgentFactory] = {}


def register_agent[F: AgentFactory](name: str) -> Callable[[F], F]:
    def decorator(factory: F) -> F:
        AGENTS[name] = factory
        return factory

    return decorator


def create_agent(name: str) -> Agent:
    try:
        return AGENTS[name]()
    except KeyError:
        raise HocError(f"no agent registered as {name!r}; known: {sorted(AGENTS)}") from None
