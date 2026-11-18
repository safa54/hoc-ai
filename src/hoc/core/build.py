"""Graph-building workspace types: brief, feedback, run control, graph tests, events.

These describe the *process* of building a graph (who asked for what, what agents noticed,
how a run is controlled). The graph itself still changes only through ChangeSets.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from hoc.core.common import Actor, StrictModel, new_id, utc_now

# ------------------------------------------------------------------------------ brief


class Brief(StrictModel):
    """What the expert wants built: the topic and its intended scope."""

    title: str = ""
    description: str = ""  # scope, level, aspects to include/exclude
    audience: str = ""
    language: str = "English"


class Area(StrictModel):
    """One coherent part of the topic that a builder works on in a single step."""

    id: str
    title: str
    description: str = ""
    key_points: list[str] = Field(default_factory=list)


class Blueprint(StrictModel):
    """The planner's decomposition of the topic. Guidance for agents, never part of the graph."""

    summary: str = ""
    grain_guide: str = ""
    areas: list[Area] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


# --------------------------------------------------------------------------- feedback


class FeedbackKind(StrEnum):
    COMMENT = "comment"
    ISSUE = "issue"
    SUGGESTION = "suggestion"
    DIRECTIVE = "directive"  # an instruction from the expert to the agents
    QUESTION = "question"  # an agent asks the expert
    ANSWER = "answer"  # the expert's reply to a question


class FeedbackStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"  # a pending proposal claims to address it
    ADDRESSED = "addressed"
    DISMISSED = "dismissed"


TargetKind = Literal["graph", "concept", "relation", "changeset", "test"]
Severity = Literal["low", "medium", "high"]


class Feedback(StrictModel):
    """A note on the graph or one of its elements, from the expert or from an agent.

    Feedback never changes the graph; agents read open feedback and answer with proposals.
    """

    id: str = Field(default_factory=lambda: new_id("fb"))
    kind: FeedbackKind = FeedbackKind.COMMENT
    target_kind: TargetKind = "graph"
    target_id: str | None = None
    text: str
    author: Actor
    severity: Severity = "medium"
    options: list[str] = Field(default_factory=list)  # suggested answers to a question
    parent_id: str | None = None  # the question an answer replies to
    status: FeedbackStatus = FeedbackStatus.OPEN
    resolution: str = ""
    changeset_id: str | None = None  # proposal that addresses (or addressed) it
    attempts: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


# ------------------------------------------------------------------------ graph tests


class GraphTest(StrictModel):
    """A question used to probe the graph: can it be answered by walking the concepts?"""

    id: str = Field(default_factory=lambda: new_id("test"))
    question: str
    expected: str = ""  # answer or solution outline, if known
    origin: Literal["expert", "agent"] = "agent"
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class TraceStep(StrictModel):
    text: str
    concept_ids: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)  # ideas needed but absent from the graph


class MissingEdge(StrictModel):
    source: str
    target: str
    reason: str = ""


class TestTrace(StrictModel):
    """One walk of a test through the graph, at a specific graph version."""

    id: str = Field(default_factory=lambda: new_id("trace"))
    test_id: str
    graph_seq: int = 0
    steps: list[TraceStep] = Field(default_factory=list)
    missing_edges: list[MissingEdge] = Field(default_factory=list)
    verdict: Literal["covered", "gaps", "not_covered"] = "gaps"
    summary: str = ""
    author: Actor | None = None
    created_at: datetime = Field(default_factory=utc_now)


# ------------------------------------------------------------------------ run control


class RunMode(StrEnum):
    MANUAL = "manual"  # every proposal waits for the expert
    ASK = "ask"  # agents apply what they are sure about and ask about the rest
    AUTO = "auto"  # everything is applied; questions never block


class RunStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"  # blocked on the expert (pending proposals or questions)
    ERROR = "error"


class RunState(StrictModel):
    status: RunStatus = RunStatus.IDLE
    mode: RunMode = RunMode.ASK
    detail: str = ""
    current_agent: str | None = None
    error: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class Progress(StrictModel):
    """Coordinator bookkeeping. Persisted so a run can stop and continue later."""

    blueprint_stale: bool = False
    built_areas: list[str] = Field(default_factory=list)
    round: int = 0
    critic_done: bool = False
    tests_generated: bool = False
    extracted_sources: list[str] = Field(default_factory=list)  # exercise sheets already read
    traced: list[str] = Field(default_factory=list)
    refine_steps: int = 0
    round_activity: int = 0
    completed: bool = False
    reviewed_seq: int = -1  # graph seq at the critic's last review
    reviewed_versions: dict[str, int] = Field(default_factory=dict)  # concept id -> version


class AgentTask(StrictModel):
    """One unit of work the coordinator hands to an agent role."""

    role: str
    label: str
    area_id: str | None = None
    test_id: str | None = None
    source_id: str | None = None  # extractor: which exercise sheet
    task_ids: list[str] = Field(default_factory=list)  # matcher: which exercises
    feedback_ids: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------- events


class BuildEvent(StrictModel):
    """Append-only record of something that happened; what the UI streams."""

    seq: int = 0
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ElementChange(StrictModel):
    kind: Literal["concept", "relation", "misconception"]
    id: str
    change: Literal["added", "updated", "removed"]
    label: str = ""
    detail: str = ""


class GraphDiff(StrictModel):
    changes: list[ElementChange] = Field(default_factory=list)

    def ids(self, change: str, kind: str = "concept") -> list[str]:
        return [c.id for c in self.changes if c.change == change and c.kind == kind]
