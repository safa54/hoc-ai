"""TargetRef: the universal address of anything assessable or believable."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict

from hoc.core.common import StrictModel


class Facet(StrEnum):
    RECALL = "recall"
    APPLY = "apply"
    EXPLAIN = "explain"
    TRANSFER = "transfer"
    CONNECT = "connect"  # only meaningful on relations


CONCEPT_FACETS: tuple[Facet, ...] = (Facet.RECALL, Facet.APPLY, Facet.EXPLAIN, Facet.TRANSFER)


class TargetKind(StrEnum):
    CONCEPT = "concept"
    RELATION = "relation"
    MISCONCEPTION = "misconception"


class TargetRef(StrictModel):
    """Points at a concept/relation/misconception, optionally at one facet of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TargetKind
    id: str
    facet: Facet | None = None

    def __str__(self) -> str:
        suffix = f".{self.facet.value}" if self.facet else ""
        return f"{self.kind.value}:{self.id}{suffix}"
