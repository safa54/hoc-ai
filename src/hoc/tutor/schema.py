"""Structured output of the one tutor call per learner message (LEARNING_SIDE_PLAN L5.2).

Flat on purpose (no optional or union types) so every provider's structured-output mode can
produce it; ``hoc.llm.schema.portable_schema`` strips the rest.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FacetName = Literal["recall", "apply", "explain", "transfer"]
OutcomeName = Literal["strong", "partial", "absent", "contradicting"]


class EvidenceItem(BaseModel):
    concept_id: str = Field(description="one of the ids shown in the context")
    facet: FacetName
    outcome: OutcomeName
    quote: str = Field(description="short exact quote from the learner's message")
    rationale: str = ""


class TutorTurnOutput(BaseModel):
    reply: str
    move: int = Field(description="index of the suggested move, or -1 for none")
    evidence: list[EvidenceItem]
    check_question: str = Field(description="a question for the learner to answer next, or ''")
    check_concept_id: str = ""
    check_facet: FacetName = "apply"
    check_expected: list[str] = Field(default_factory=list)
    check_result: Literal["", "strong", "partial", "absent", "contradicting"] = Field(
        description="verdict on the pending check question, if the message answers it"
    )
    note: str = ""
    graph_gap: str = ""
    highlight: list[str] = Field(
        default_factory=list, description="ids from the context to light up on the map, or []"
    )
