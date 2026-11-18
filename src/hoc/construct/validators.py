"""Deterministic graph checks (ARCHITECTURE §5.2 stage 8). Cheap, run without any LLM."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import networkx as nx

from hoc.core.knowledge import Necessity, Relation, RelationType
from hoc.store.knowledge_graph import KnowledgeGraph


@dataclass(frozen=True)
class Issue:
    target_kind: Literal["graph", "concept", "relation"]
    target_id: str | None
    severity: Literal["low", "medium", "high"]
    text: str
    check: str


Validator = Callable[[KnowledgeGraph], list[Issue]]
VALIDATORS: dict[str, Validator] = {}


def validator(name: str) -> Callable[[Validator], Validator]:
    def decorator(fn: Validator) -> Validator:
        VALIDATORS[name] = fn
        return fn

    return decorator


@validator("orphans")
def orphans(graph: KnowledgeGraph) -> list[Issue]:
    if len(graph.concepts()) < 4:
        return []
    linked = {x for r in graph.relations() for x in (r.source, r.target)}
    return [
        Issue(
            "concept",
            c.id,
            "medium",
            f"'{c.name}' has no relations to any other concept.",
            "orphans",
        )
        for c in graph.concepts()
        if c.id not in linked
    ]


@validator("thin_cards")
def thin_cards(graph: KnowledgeGraph) -> list[Issue]:
    return [
        Issue(
            "concept",
            c.id,
            "low",
            f"'{c.name}' lacks a definition or key ideas; its content is too thin to teach "
            "or assess.",
            "thin_cards",
        )
        for c in graph.concepts()
        if not c.card.definition or not c.card.key_ideas
    ]


def redundant_prerequisite_edges(graph: KnowledgeGraph) -> list[Relation]:
    """Prerequisite edges implied by a longer chain that is at least as strong: an essential
    edge needs an all-essential chain (the learning frontier follows essential edges, so a
    weaker chain would not replace it); a supportive edge is implied by any chain.
    All of them can be removed together without changing what is reachable."""
    out = []
    for essential_only in (True, False):
        g = graph.prerequisite_graph(essential_only=essential_only)
        for r in graph.relations(RelationType.PREREQUISITE_OF):
            if (r.necessity is Necessity.ESSENTIAL) is not essential_only:
                continue
            g.remove_edge(r.source, r.target)
            implied = nx.has_path(g, r.source, r.target)
            g.add_edge(r.source, r.target)
            if implied:
                out.append(r)
    return sorted(out, key=lambda r: r.id)


@validator("redundant_prerequisites")
def redundant_prerequisites(graph: KnowledgeGraph) -> list[Issue]:
    return [
        Issue(
            "relation",
            r.id,
            "low",
            f"{r.source} -> {r.target} is implied by a longer prerequisite chain; "
            "consider removing it.",
            "redundant_prerequisites",
        )
        for r in redundant_prerequisite_edges(graph)
    ]


@validator("hubs")
def hubs(graph: KnowledgeGraph) -> list[Issue]:
    g = graph.prerequisite_graph()
    return [
        Issue(
            "concept",
            node,
            "medium",
            f"'{node}' has {deg} direct prerequisites; it may be too coarse and worth splitting.",
            "hubs",
        )
        for node, deg in g.in_degree()
        if deg > 6
    ]


@validator("weak_relations")
def weak_relations(graph: KnowledgeGraph) -> list[Issue]:
    rels = graph.relations()
    weak = [r for r in rels if r.type is RelationType.RELATED]
    if len(rels) >= 5 and len(weak) / len(rels) > 0.2:
        return [
            Issue(
                "graph",
                None,
                "low",
                f"{len(weak)} of {len(rels)} relations are the weak type 'related'; "
                "replace them with precise types.",
                "weak_relations",
            )
        ]
    return []


def run_validators(graph: KnowledgeGraph, names: list[str] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for name, check in VALIDATORS.items():
        if names is None or name in names:
            issues += check(graph)
    return issues
