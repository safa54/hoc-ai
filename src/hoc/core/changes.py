"""ChangeSet and GraphOp: the only way the knowledge graph is allowed to change.

Ops are *data* (this module). How they are applied lives in ``hoc.store.apply``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field

from hoc.core.common import Actor, StrictModel, new_id, utc_now
from hoc.core.knowledge import Concept, Misconception, Necessity, Relation, RelationType
from hoc.core.provenance import EvidenceLink

# ------------------------------------------------------------------------------ ops


class _OpBase(StrictModel):
    note: str = ""  # optional per-op explanation; the ChangeSet carries the main rationale


class AddConcept(_OpBase):
    op: Literal["add_concept"] = "add_concept"
    concept: Concept


class UpdateConcept(_OpBase):
    """Patch top-level fields: name, aliases, kind, topic_ids."""

    op: Literal["update_concept"] = "update_concept"
    concept_id: str
    patch: dict[str, Any]


class UpdateConceptCard(_OpBase):
    """Field-level patch of the concept card (keys are ConceptCard field names)."""

    op: Literal["update_concept_card"] = "update_concept_card"
    concept_id: str
    patch: dict[str, Any]


class MergeConcepts(_OpBase):
    op: Literal["merge_concepts"] = "merge_concepts"
    from_id: str
    into_id: str


class SplitConcept(_OpBase):
    op: Literal["split_concept"] = "split_concept"
    from_id: str
    into: list[Concept] = Field(min_length=2)


class DeprecateConcept(_OpBase):
    op: Literal["deprecate_concept"] = "deprecate_concept"
    concept_id: str


class AddRelation(_OpBase):
    op: Literal["add_relation"] = "add_relation"
    relation: Relation


class UpdateRelation(_OpBase):
    """Patch necessity, rationale, confidence, scope (not type or endpoints: use retype)."""

    op: Literal["update_relation"] = "update_relation"
    relation_id: str
    patch: dict[str, Any]


class RetypeRelation(_OpBase):
    op: Literal["retype_relation"] = "retype_relation"
    relation_id: str
    new_type: RelationType
    necessity: Necessity | None = None


class RemoveRelation(_OpBase):
    op: Literal["remove_relation"] = "remove_relation"
    relation_id: str


class AddMisconception(_OpBase):
    op: Literal["add_misconception"] = "add_misconception"
    misconception: Misconception


class LinkMisconception(_OpBase):
    op: Literal["link_misconception"] = "link_misconception"
    misconception_id: str
    concept_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)


GraphOp = Annotated[
    AddConcept
    | UpdateConcept
    | UpdateConceptCard
    | MergeConcepts
    | SplitConcept
    | DeprecateConcept
    | AddRelation
    | UpdateRelation
    | RetypeRelation
    | RemoveRelation
    | AddMisconception
    | LinkMisconception,
    Field(discriminator="op"),
]

# Ops that restructure existing accepted knowledge: triage always sends these to review.
STRUCTURAL_OPS = frozenset({"merge_concepts", "split_concept", "deprecate_concept"})


# ------------------------------------------------------------------------- changeset


class ChangeSetStatus(StrEnum):
    PROPOSED = "proposed"
    AUTO_APPLIED = "auto_applied"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    REVERTED = "reverted"

    @property
    def is_applied(self) -> bool:
        return self in (ChangeSetStatus.AUTO_APPLIED, ChangeSetStatus.APPROVED)


class ChangeOrigin(StrEnum):
    SOURCE = "source"
    SOLUTION = "solution"
    CURATOR = "curator"
    EXPERT = "expert"
    LEARNER_AGGREGATE = "learner_aggregate"
    SYSTEM = "system"


class ChangeSet(StrictModel):
    id: str = Field(default_factory=lambda: new_id("cs"))
    author: Actor
    origin: ChangeOrigin = ChangeOrigin.SYSTEM
    rationale: str
    ops: list[GraphOp] = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    impact: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: list[EvidenceLink] = Field(default_factory=list)
    run_id: str | None = None
    review_item_id: str | None = None
    status: ChangeSetStatus = ChangeSetStatus.PROPOSED
    applied_seq: int | None = None  # monotonically increasing order of application
    created_at: datetime = Field(default_factory=utc_now)
    applied_at: datetime | None = None
    decided_by: Actor | None = None  # who approved / rejected
