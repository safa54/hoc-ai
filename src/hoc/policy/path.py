"""Learning path: one coherent order through the graph (or towards a goal concept).

A plain topological sort respects prerequisites but hops between unrelated areas. This order
also respects prerequisites and, among the concepts that are ready, prefers to
  1. continue with something the concept just placed leads to,
  2. stay in the area being worked on until nothing in it is ready,
  3. otherwise open the earliest area (planner order) that has something ready.
Pure and deterministic: the policy scores "advance" moves against it and the interface draws
it, so what the learner sees and what the tutor suggests are the same path.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from hoc.core.errors import NotFoundError
from hoc.store.knowledge_graph import KnowledgeGraph


def _area(graph: KnowledgeGraph, concept_id: str) -> str:
    topics = graph.concept(concept_id).topic_ids
    return topics[0] if topics else ""


def learning_path(
    graph: KnowledgeGraph,
    *,
    goal: str | None = None,
    area_order: Sequence[str] = (),
    visible: Collection[str] | None = None,
) -> list[str]:
    """Concept ids in the order to learn them. With a ``goal``: only the goal and what it
    essentially depends on, ending at the goal. An unknown goal means no goal."""
    nodes = {c.id for c in graph.concepts()}
    if visible is not None:
        nodes &= set(visible)
    if goal is not None:
        try:
            goal = graph.resolve(goal)
        except NotFoundError:
            goal = None
    if goal is not None and goal in nodes:
        nodes &= graph.prerequisite_closure([goal], essential_only=True)
    g = graph.prerequisite_graph().subgraph(nodes)
    base = [c for c in graph.learning_order() if c in nodes]
    position = {c: i for i, c in enumerate(base)}
    area = {c: _area(graph, c) for c in nodes}

    rank = {a: i for i, a in enumerate(area_order)}
    for c in base:  # areas the planner does not know: in order of first appearance
        rank.setdefault(area[c], len(rank))

    waiting = {c: g.in_degree(c) for c in nodes}
    ready = {c for c, n in waiting.items() if n == 0}
    path: list[str] = []
    last: str | None = None
    current: str | None = None
    while ready:
        here = [c for c in ready if area[c] == current] if current is not None else []
        if here:
            follows = set(g.successors(last)) if last is not None else set()
            nxt = min(here, key=lambda c: (c not in follows, position[c]))
        else:  # opening an area: the earliest one, at its earliest ready concept
            nxt = min(ready, key=lambda c: (rank[area[c]], position[c]))
        ready.discard(nxt)
        path.append(nxt)
        last, current = nxt, area[nxt]
        for s in g.successors(nxt):
            waiting[s] -= 1
            if waiting[s] == 0:
                ready.add(s)
    return path
