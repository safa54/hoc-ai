"""Assemble a ``LearnerState`` from evidence and interactions (model-independent parts).

"Covered" (visited) and "understood" (belief) are kept apart on purpose: opening a node is an
interaction, never evidence (invariant L1.2).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from hoc.config import LearnSettings
from hoc.core.common import utc_now
from hoc.core.knowledge import Necessity, RelationType
from hoc.core.learner import Observation
from hoc.core.learning import (
    Belief,
    BeliefStatus,
    ConceptState,
    Flag,
    Interaction,
    LearnerState,
)
from hoc.core.refs import CONCEPT_FACETS, Facet
from hoc.learner.base import StudentModel
from hoc.learner.resolve import concept_index, resolve_concept, resolve_observations
from hoc.store.knowledge_graph import KnowledgeGraph

_UNDERSTOOD = (BeliefStatus.LIKELY, BeliefStatus.MASTERED)


def concept_status(facets: dict[str, Belief]) -> BeliefStatus:
    """The one colour a node gets (LEARNING_SIDE_PLAN L4.3). Table-driven test: tune freely.

    Differs from the plan in one place: ``likely`` needs *apply or explain* to be understood
    (the plan said apply), so a learner who explained a concept well is not stuck at "shaky".
    """
    observed = {f: b for f, b in facets.items() if b.status is not BeliefStatus.UNKNOWN}
    if not observed:
        return BeliefStatus.UNKNOWN
    if any(b.status is BeliefStatus.LIKELY_NOT for b in observed.values()):
        return BeliefStatus.LIKELY_NOT
    apply_b = facets.get(Facet.APPLY.value)
    explain_b = facets.get(Facet.EXPLAIN.value)
    if (
        apply_b is not None
        and explain_b is not None
        and apply_b.status is BeliefStatus.MASTERED
        and explain_b.status is BeliefStatus.MASTERED
    ):
        return BeliefStatus.MASTERED
    core = [b for b in (apply_b, explain_b) if b is not None]
    if any(b.status in _UNDERSTOOD for b in core):
        return BeliefStatus.LIKELY
    return BeliefStatus.UNCERTAIN


def concept_mean(facets: dict[str, Belief]) -> float | None:
    """Best single summary of an observed concept (apply if observed, else best facet)."""
    apply_b = facets.get(Facet.APPLY.value)
    if apply_b is not None and apply_b.observed:
        return apply_b.mean
    means = [b.mean for b in facets.values() if b.observed]
    return max(means) if means else None


def config_hash(settings: LearnSettings) -> str:
    payload = settings.model_dump(include={"beliefs", "judge_reliability", "split_evidence_weight"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def build_state(
    learner_id: str,
    observations: list[Observation],
    interactions: list[Interaction],
    graph: KnowledgeGraph,
    model: StudentModel,
    settings: LearnSettings,
    *,
    graph_seq: int = 0,
    at: datetime | None = None,
) -> LearnerState:
    at = at or utc_now()
    by_id = concept_index(graph)
    resolved = resolve_observations(observations, graph, settings.split_evidence_weight)
    beliefs = model.compute(resolved, at, settings.beliefs)

    last_evidence: dict[str, datetime] = {}
    counts: dict[str, int] = {}
    for r in resolved:
        counts[r.concept_id] = counts.get(r.concept_id, 0) + 1
        ts = r.observation.timestamp
        if r.concept_id not in last_evidence or ts > last_evidence[r.concept_id]:
            last_evidence[r.concept_id] = ts

    visited: set[str] = set()
    flags: dict[str, Flag | None] = {}
    for item in interactions:  # chronological
        if item.concept_id is None:
            continue
        for cid, _, _ in resolve_concept(by_id, item.concept_id, 1.0):
            if item.kind in ("visit", "read"):
                visited.add(cid)
            elif item.kind == "flag":
                raw = item.meta.get("flag")
                flags[cid] = Flag(raw) if raw else None

    concepts: dict[str, ConceptState] = {}
    for c in graph.concepts():
        per = beliefs.get(c.id, {})
        facets = {f.value: per.get(f, Belief()) for f in CONCEPT_FACETS}
        concepts[c.id] = ConceptState(
            concept_id=c.id,
            status=concept_status(facets),
            facets=facets,
            visited=c.id in visited,
            self_flag=flags.get(c.id),
            last_evidence_at=last_evidence.get(c.id),
            n_observations=counts.get(c.id, 0),
        )

    # a hint from prerequisites for concepts we know nothing about (never evidence, never colour)
    essential: dict[str, list[str]] = {}
    for rel in graph.relations(RelationType.PREREQUISITE_OF):
        if rel.necessity is Necessity.ESSENTIAL:
            essential.setdefault(rel.target, []).append(rel.source)
    for cid, st in concepts.items():
        if st.status is not BeliefStatus.UNKNOWN:
            continue
        means = [
            m
            for p in essential.get(cid, [])
            if p in concepts and (m := concept_mean(concepts[p].facets)) is not None
        ]
        if means:
            st.inferred_mean = round(min(means), 4)

    return LearnerState(
        learner_id=learner_id,
        model_id=model.id,
        config_hash=config_hash(settings),
        graph_seq=graph_seq,
        obs_count=len(observations),
        interaction_count=len(interactions),
        concepts=concepts,
        computed_at=at,
    )
