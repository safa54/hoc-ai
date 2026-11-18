"""Learner layer: evidence is immutable, state is derived (ARCHITECTURE §4.4)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from hoc.core.common import StrictModel, new_id, utc_now
from hoc.core.refs import TargetRef


class Learner(StrictModel):
    id: str = Field(default_factory=lambda: new_id("lrn"))
    display_name: str
    is_simulated: bool = False
    sim_profile_id: str | None = None


class Session(StrictModel):
    id: str = Field(default_factory=lambda: new_id("ses"))
    learner_id: str
    objective_id: str | None = None  # free exploration has no objective
    graph_snapshot_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None


class Turn(StrictModel):
    """Raw message. Never edited."""

    id: str = Field(default_factory=lambda: new_id("trn"))
    episode_id: str | None = None
    session_id: str | None = None
    learner_id: str | None = None
    role: Literal["tutor", "learner"]
    text: str
    hint_level: int = 0
    focus_concept_id: str | None = None
    intent: str | None = None
    llm_call_id: str | None = None
    decision_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)  # context sizes, raw output, cost: debug
    timestamp: datetime = Field(default_factory=utc_now)


class Episode(StrictModel):
    id: str = Field(default_factory=lambda: new_id("epi"))
    session_id: str
    action_type: str
    action_targets: list[TargetRef] = Field(default_factory=list)
    task_id: str | None = None
    decision_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)


class Outcome(StrEnum):
    STRONG = "strong"
    PARTIAL = "partial"
    ABSENT = "absent"
    CONTRADICTING = "contradicting"


class JudgeKind(StrEnum):
    EXACT = "exact"
    RULE = "rule"
    LLM = "llm"
    SELF_REPORT = "self_report"


class Judge(StrictModel):
    kind: JudgeKind
    id: str
    reliability: float = Field(ge=0.0, le=1.0)


class Observation(StrictModel):
    """The unit of evidence. Append-only."""

    id: str = Field(default_factory=lambda: new_id("obs"))
    learner_id: str
    episode_id: str | None = None
    target: TargetRef
    outcome: Outcome
    weight: float = Field(ge=0.0)
    judge: Judge
    rationale: str = ""
    quote: str | None = None
    task_id: str | None = None
    approach_id: str | None = None
    hints_used: int = 0
    timestamp: datetime = Field(default_factory=utc_now)


class AssertionValidity(StrEnum):
    CANONICAL = "canonical"
    VALID_NOVEL = "valid_novel"
    PARTIALLY_VALID = "partially_valid"
    INVALID = "invalid"


class LearnerAssertion(StrictModel):
    """A connection or interpretation the learner actually stated."""

    id: str = Field(default_factory=lambda: new_id("asr"))
    learner_id: str
    episode_id: str | None = None
    text: str
    mapped_refs: list[TargetRef] = Field(default_factory=list)
    validity: AssertionValidity
    linked_misconception_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)


class PolicyDecision(StrictModel):
    """Every episode's decision with all candidates and score components."""

    id: str = Field(default_factory=lambda: new_id("dec"))
    session_id: str | None = None
    learner_id: str | None = None
    focus_concept_id: str | None = None
    policy_id: str
    candidates: list[dict[str, Any]]
    chosen_index: int = -1
    chosen_by: Literal["policy", "tutor", "learner"] = "policy"
    accepted: bool | None = None  # did the learner follow a suggested move
    timestamp: datetime = Field(default_factory=utc_now)
