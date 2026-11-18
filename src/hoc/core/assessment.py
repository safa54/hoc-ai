"""Assessment & pedagogy layer: tasks, solution paths, objectives (ARCHITECTURE §4.3)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from hoc.core.common import StrictModel, new_id
from hoc.core.refs import TargetRef


class TaskType(StrEnum):
    MCQ = "mcq"
    NUMERIC = "numeric"
    SHORT_ANSWER = "short_answer"
    EXPLAIN_WHY = "explain_why"
    PREDICT = "predict"
    COMPARE = "compare"
    FIND_COUNTEREXAMPLE = "find_counterexample"
    TRANSFER_PROBLEM = "transfer_problem"
    WORKED_PROBLEM = "worked_problem"


class Requirement(StrictModel):
    """A target needed by an approach; primary counts fully, secondary half."""

    ref: TargetRef
    weight: Literal["primary", "secondary"] = "primary"


class Approach(StrictModel):
    """One way of solving the task: an AND-set of requirements."""

    id: str
    label: str = ""
    requirements: list[Requirement]


class MisconceptionDiagnostic(StrictModel):
    pattern: str  # description of the response pattern
    misconception_id: str


class Rubric(StrictModel):
    expected_key_ideas: list[str] = Field(default_factory=list)  # e.g. "bayes-rule#KI2"
    correct_answers: list[str] = Field(default_factory=list)
    partial_credit: str = ""
    misconception_diagnostics: list[MisconceptionDiagnostic] = Field(default_factory=list)


class TaskStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    RETIRED = "retired"


class Task(StrictModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    title: str = ""
    prompt: str
    task_type: TaskType
    approaches: list[Approach] = Field(default_factory=list)  # alternatives of AND-sets
    alternatives_known: bool = False  # False: approaches are sufficient, not proven necessary
    rubric: Rubric = Field(default_factory=Rubric)
    difficulty_prior: float = Field(default=0.5, ge=0.0, le=1.0)
    origin: Literal["source", "generated", "expert"] = "generated"
    status: TaskStatus = TaskStatus.DRAFT
    options: list[str] = Field(default_factory=list)  # for MCQ
    solution_text: str = ""
    source_id: str | None = None  # material the task was extracted from
    match_status: Literal["unmatched", "proposed", "confirmed", "rejected"] = "unmatched"
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unmapped: list[str] = Field(default_factory=list)  # ideas the graph lacks


class StepType(StrEnum):
    SETUP = "setup"
    TRANSFORM = "transform"
    COMPUTE = "compute"
    JUSTIFY = "justify"
    CONCLUDE = "conclude"


class StepUse(StrictModel):
    ref: TargetRef | None = None  # None => unmapped
    role: Literal["invoked", "recognised", "justified_by"] = "invoked"
    unmapped_description: str | None = None


class SolutionStep(StrictModel):
    index: int
    text: str
    step_type: StepType
    uses: list[StepUse] = Field(default_factory=list)


class SolutionPath(StrictModel):
    id: str = Field(default_factory=lambda: new_id("sol"))
    task_id: str
    approach_label: str = ""
    steps: list[SolutionStep]
    source_chunk_id: str | None = None  # None => generated


class LearningObjective(StrictModel):
    id: str
    name: str
    targets: list[TargetRef]
    scope_note: str = ""
