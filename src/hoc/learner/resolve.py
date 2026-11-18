"""Stable identity under graph change (ARCHITECTURE §4.6): evidence recorded against a concept
survives merges and splits. Done in one place so every model sees the same resolution."""

from __future__ import annotations

from hoc.core.knowledge import Concept, ConceptStatus
from hoc.core.learner import Observation
from hoc.core.refs import TargetKind
from hoc.learner.base import ResolvedObservation
from hoc.store.knowledge_graph import KnowledgeGraph

_MAX_DEPTH = 8


def concept_index(graph: KnowledgeGraph) -> dict[str, Concept]:
    return {c.id: c for c in graph.concepts(include_inactive=True)}


def resolve_concept(
    by_id: dict[str, Concept], concept_id: str, split_weight: float
) -> list[tuple[str, float, bool]]:
    """Active concepts a (possibly retired) concept id now stands for, with weight multipliers.

    merged -> its survivor (full weight); split -> every successor at ``split_weight`` (marked
    inferred); deprecated without successors or unknown -> nothing (kept in the log, ignored).
    """
    out: list[tuple[str, float, bool]] = []

    def walk(cid: str, mult: float, inferred: bool, depth: int) -> None:
        c = by_id.get(cid)
        if c is None or depth > _MAX_DEPTH:
            return
        if c.redirect_to:
            walk(c.redirect_to, mult, inferred, depth + 1)
        elif c.status is ConceptStatus.DEPRECATED:
            for successor in c.split_into:
                walk(successor, mult * split_weight, True, depth + 1)
        else:
            out.append((cid, mult, inferred))

    walk(concept_id, 1.0, False, 0)
    return out


def resolve_observations(
    observations: list[Observation], graph: KnowledgeGraph, split_weight: float
) -> list[ResolvedObservation]:
    by_id = concept_index(graph)
    resolved: list[ResolvedObservation] = []
    for obs in observations:
        if obs.target.kind is not TargetKind.CONCEPT:
            continue
        for cid, mult, inferred in resolve_concept(by_id, obs.target.id, split_weight):
            resolved.append(ResolvedObservation(obs, cid, mult, inferred))
    return resolved
