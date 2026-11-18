"""Learning frontier (ARCHITECTURE §9.1): what is ready to be learned next."""

from __future__ import annotations

from hoc.config import LearnSettings
from hoc.core.knowledge import Necessity, RelationType
from hoc.core.learning import BeliefStatus, LearnerState
from hoc.store.knowledge_graph import KnowledgeGraph

_WEAKNESS = {
    BeliefStatus.LIKELY_NOT: 1.0,
    BeliefStatus.UNCERTAIN: 0.6,
    BeliefStatus.UNKNOWN: 0.5,
    BeliefStatus.LIKELY: 0.1,
    BeliefStatus.MASTERED: 0.0,
}


def weakness(status: BeliefStatus) -> float:
    return _WEAKNESS[status]


def essential_prerequisites(graph: KnowledgeGraph, concept_id: str) -> list[str]:
    return sorted(
        r.source
        for r in graph.relations(RelationType.PREREQUISITE_OF)
        if r.target == concept_id and r.necessity is Necessity.ESSENTIAL
    )


def dependents(graph: KnowledgeGraph, concept_id: str) -> list[str]:
    return sorted(
        r.target for r in graph.relations(RelationType.PREREQUISITE_OF) if r.source == concept_id
    )


def prerequisite_in_place(state: LearnerState, concept_id: str, settings: LearnSettings) -> bool:
    st = state.concept(concept_id)
    if st.status.understood:
        return True
    if st.status is BeliefStatus.UNKNOWN and st.inferred_mean is not None:
        return st.inferred_mean >= settings.ready_threshold
    return False


def frontier(graph: KnowledgeGraph, state: LearnerState, settings: LearnSettings) -> list[str]:
    """Concepts not yet understood whose essential prerequisites are all in place.

    A concept with no essential prerequisites is always ready, so a new learner starts at the
    roots of the graph. Returned in a stable learning order.
    """
    ready: list[str] = []
    order = graph.learning_order()
    for cid in order:
        if state.concept(cid).status.understood:
            continue
        if all(
            prerequisite_in_place(state, p, settings) for p in essential_prerequisites(graph, cid)
        ):
            ready.append(cid)
    return ready
