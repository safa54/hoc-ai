"""Knowledge layer: concepts, relations, misconceptions, topics (ARCHITECTURE §4.1)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from hoc.core.common import SLUG_PATTERN, Actor, StrictModel, utc_now

# --------------------------------------------------------------------------- concepts


class ConceptKind(StrEnum):
    CONCEPT = "concept"  # what something is
    PRINCIPLE = "principle"  # a relationship that holds (theorem, law, identity)
    PROCEDURE = "procedure"  # a method or skill


class ConceptStatus(StrEnum):
    PROPOSED = "proposed"  # in the graph, not yet confirmed by an expert
    ACCEPTED = "accepted"
    DEPRECATED = "deprecated"
    MERGED = "merged"

    @property
    def is_active(self) -> bool:
        return self in (ConceptStatus.PROPOSED, ConceptStatus.ACCEPTED)


class KeyIdea(StrictModel):
    """Atomic proposition about a concept; the finest unit of evidence referenced by rubrics."""

    id: str = Field(pattern=r"^KI\d+$")
    text: str


class FacetIndicators(StrictModel):
    """What demonstrating each facet looks like for *this* concept (its evidence model)."""

    recall: str = ""
    apply: str = ""
    explain: str = ""
    transfer: str = ""


class ConceptCard(StrictModel):
    """The AI-readable core meaning of a concept."""

    one_line: str = ""
    definition: str = ""
    formal: str | None = None
    key_ideas: list[KeyIdea] = Field(default_factory=list)
    representations: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    non_examples: list[str] = Field(default_factory=list)
    scope_conditions: str = ""
    facet_indicators: FacetIndicators = Field(default_factory=FacetIndicators)
    misconception_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_key_idea_ids(self) -> Self:
        ids = [k.id for k in self.key_ideas]
        if len(ids) != len(set(ids)):
            raise ValueError("key idea ids must be unique within a card")
        return self


class Concept(StrictModel):
    id: str = Field(pattern=SLUG_PATTERN)
    name: str
    aliases: list[str] = Field(default_factory=list)
    kind: ConceptKind = ConceptKind.CONCEPT
    card: ConceptCard = Field(default_factory=ConceptCard)
    topic_ids: list[str] = Field(default_factory=list)
    status: ConceptStatus = ConceptStatus.PROPOSED
    locked: bool = False  # expert-confirmed: agents may only *propose* changes
    version: int = 1
    redirect_to: str | None = None  # set when status == MERGED
    split_into: list[str] = Field(default_factory=list)  # set when split & deprecated
    created_by: Actor | None = None
    created_at: datetime = Field(default_factory=utc_now)


# --------------------------------------------------------------------------- relations


class RelationType(StrEnum):
    PREREQUISITE_OF = "prerequisite_of"
    SPECIALIZES = "specializes"
    PART_OF = "part_of"
    CONTRASTS_WITH = "contrasts_with"
    ANALOGOUS_TO = "analogous_to"
    RELATED = "related"


class Necessity(StrEnum):
    ESSENTIAL = "essential"  # definitional test: cannot understand B without A
    SUPPORTIVE = "supportive"  # A substantially eases B; other routes exist


class EdgeSpec(StrictModel):
    """Properties of an edge type. New types are added here, not in the Relation schema."""

    symmetric: bool = False
    acyclic: bool = False
    needs_necessity: bool = False


EDGE_REGISTRY: dict[RelationType, EdgeSpec] = {
    RelationType.PREREQUISITE_OF: EdgeSpec(acyclic=True, needs_necessity=True),
    RelationType.SPECIALIZES: EdgeSpec(acyclic=True),
    RelationType.PART_OF: EdgeSpec(acyclic=True),
    RelationType.CONTRASTS_WITH: EdgeSpec(symmetric=True),
    RelationType.ANALOGOUS_TO: EdgeSpec(symmetric=True),
    RelationType.RELATED: EdgeSpec(symmetric=True),
}


class RelationStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REMOVED = "removed"  # soft delete: history is preserved, re-adding revives it

    @property
    def is_active(self) -> bool:
        return self is not RelationStatus.REMOVED


def make_relation_id(type_: RelationType, source: str, target: str) -> str:
    """Relations have a natural key, so the same edge can never exist twice."""
    return f"{type_.value}:{source}->{target}"


class EvidenceSummary(StrictModel):
    """Counts of evidence by origin. Kept separate so provenance is never conflated."""

    from_text: int = 0
    from_solutions: int = 0
    from_expert: int = 0
    from_learners: int = 0


class Relation(StrictModel):
    id: str = ""  # derived from (type, source, target); see validator
    type: RelationType
    source: str
    target: str
    necessity: Necessity | None = None
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: RelationStatus = RelationStatus.PROPOSED
    locked: bool = False
    scope: list[str] = Field(default_factory=list)  # objective ids; empty = global
    evidence_summary: EvidenceSummary = Field(default_factory=EvidenceSummary)
    source_refs: list[str] = Field(default_factory=list)
    version: int = 1
    created_by: Actor | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _normalise(self) -> Self:
        spec = EDGE_REGISTRY[self.type]
        if self.source == self.target:
            raise ValueError("self-loops are not allowed")
        if spec.needs_necessity and self.necessity is None:
            raise ValueError(f"{self.type.value} requires a necessity")
        if not spec.needs_necessity and self.necessity is not None:
            raise ValueError(f"{self.type.value} must not carry a necessity")
        if spec.symmetric and self.source > self.target:  # canonical order
            self.source, self.target = self.target, self.source
        self.id = make_relation_id(self.type, self.source, self.target)
        return self


# ----------------------------------------------------------------- misconceptions / topics


class Misconception(StrictModel):
    id: str = Field(pattern=SLUG_PATTERN)
    name: str
    statement: str  # the incorrect belief as the learner would hold it
    concept_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    diagnostic_signs: list[str] = Field(default_factory=list)
    diagnostic_task_ids: list[str] = Field(default_factory=list)
    remediation_notes: str = ""
    origin: Literal["source", "expert", "agent", "learner_aggregate"] = "agent"
    version: int = 1
    created_by: Actor | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Topic(StrictModel):
    """Navigation grouping only; never part of the dependency structure."""

    id: str = Field(pattern=SLUG_PATTERN)
    name: str
    parent_id: str | None = None
