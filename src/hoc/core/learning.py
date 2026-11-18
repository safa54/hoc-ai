"""Learning side types (LEARNING_SIDE_PLAN L3.2).

Evidence (``Observation``) is immutable and lives in ``hoc.core.learner``. Everything here is
either *derived* from evidence (beliefs, state, moves) or a record of what happened in the
interface (interactions, attempts).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from hoc.core.common import StrictModel, new_id, utc_now
from hoc.core.learner import Observation, Turn
from hoc.core.refs import Facet, TargetRef

# ----------------------------------------------------------------------------- beliefs


class BeliefStatus(StrEnum):
    UNKNOWN = "unknown"  # not enough evidence — structurally distinct from LIKELY_NOT
    LIKELY_NOT = "likely_not"
    UNCERTAIN = "uncertain"
    LIKELY = "likely"
    MASTERED = "mastered"

    @property
    def understood(self) -> bool:
        return self in (BeliefStatus.LIKELY, BeliefStatus.MASTERED)


class Belief(StrictModel):
    mean: float = 0.5
    alpha: float = 1.0
    beta: float = 1.0
    evidence_mass: float = 0.0
    status: BeliefStatus = BeliefStatus.UNKNOWN
    observed: bool = False


class BeliefContribution(StrictModel):
    """How one observation moved one facet belief (for the "why do we think this?" view)."""

    observation_id: str
    outcome: str
    facet: Facet
    delta_alpha: float
    delta_beta: float
    weight: float
    decay: float
    quote: str | None = None
    rationale: str = ""
    judge: str = ""
    timestamp: datetime
    inferred: bool = False  # came via split inheritance or facet propagation


class Flag(StrEnum):
    GOT_IT = "got_it"
    LOST = "lost"


class ConceptState(StrictModel):
    concept_id: str
    status: BeliefStatus = BeliefStatus.UNKNOWN
    facets: dict[str, Belief] = Field(default_factory=dict)  # keyed by Facet value
    visited: bool = False
    self_flag: Flag | None = None
    inferred_mean: float | None = None  # from prerequisites; a hint, never evidence
    last_evidence_at: datetime | None = None
    n_observations: int = 0


class LearnerState(StrictModel):
    """Derived and disposable: recompute from observations at any time."""

    learner_id: str
    model_id: str
    config_hash: str = ""
    graph_seq: int = 0
    obs_count: int = 0
    interaction_count: int = 0
    concepts: dict[str, ConceptState] = Field(default_factory=dict)
    computed_at: datetime = Field(default_factory=utc_now)

    def concept(self, concept_id: str) -> ConceptState:
        return self.concepts.get(concept_id) or ConceptState(concept_id=concept_id)


# ------------------------------------------------------------------------ interactions


class Interaction(StrictModel):
    """Something the learner did in the interface. Never evidence by itself (invariant L1.2)."""

    id: str = Field(default_factory=lambda: new_id("int"))
    learner_id: str
    kind: Literal["visit", "read", "chip_accept", "chip_dismiss", "flag", "task_open", "goal"]
    concept_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------- policy


MoveKind = Literal[
    "stay", "review_prerequisite", "use_analogy", "check", "practice", "advance", "overview"
]


class Move(StrictModel):
    """One candidate pedagogical action. Pure data: the tutor renders it, the UI links it."""

    kind: MoveKind
    concept_id: str | None = None
    concept_name: str = ""
    facet: Facet | None = None
    task_id: str | None = None
    reason: str = ""
    score: float = 0.0
    components: dict[str, float] = Field(default_factory=dict)
    rank: int = -1  # position in the logged decision's candidate list

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.kind, self.concept_id or self.task_id)


# ------------------------------------------------------------------------------ tutor


class Intent(StrEnum):
    MESSAGE = "message"
    EXPLAIN = "explain"
    WHY_NEEDED = "why_needed"
    QUIZ = "quiz"
    EXERCISE = "exercise"
    NEXT = "next"
    GOT_IT = "got_it"
    LOST = "lost"
    HINT = "hint"
    OVERVIEW = "overview"


class PendingCheck(StrictModel):
    question: str
    concept_id: str
    facet: Facet = Facet.APPLY
    expected: list[str] = Field(default_factory=list)
    turn_id: str | None = None
    resolved: bool = False


class NoteEntry(StrictModel):
    text: str
    turn_id: str | None = None
    at: datetime = Field(default_factory=utc_now)


class LearnerNotes(StrictModel):
    """The tutor's running narrative. Context for the tutor only — never an input to beliefs."""

    learner_id: str
    entries: list[NoteEntry] = Field(default_factory=list)


class CallCost(StrictModel):
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    ms: int = 0
    cached: bool = False
    calls: int = 0  # real (non-cached) LLM calls this turn: 0 or 1


class TutorReply(StrictModel):
    turn: Turn
    suggestions: list[Move] = Field(default_factory=list)
    decision_id: str | None = None
    observations: list[Observation] = Field(default_factory=list)
    check: PendingCheck | None = None
    changed: list[str] = Field(default_factory=list)  # concepts whose status changed
    highlight: list[str] = Field(default_factory=list)  # concepts to light up on the map
    cost: CallCost = Field(default_factory=CallCost)


class TaskAttempt(StrictModel):
    id: str = Field(default_factory=lambda: new_id("att"))
    learner_id: str
    task_id: str
    answer: str = ""
    hints_used: int = 0
    correct: bool | None = None
    feedback: str = ""
    revealed: bool = False
    graded_by: str = ""  # "exact" | "llm"
    llm_call_id: str | None = None
    approach_id: str | None = None
    observation_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


__all__ = [
    "Belief",
    "BeliefContribution",
    "BeliefStatus",
    "CallCost",
    "ConceptState",
    "Flag",
    "Intent",
    "Interaction",
    "LearnerNotes",
    "LearnerState",
    "Move",
    "NoteEntry",
    "Observation",
    "PendingCheck",
    "TargetRef",
    "TaskAttempt",
    "TutorReply",
]
