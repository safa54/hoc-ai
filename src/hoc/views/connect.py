"""Graph algorithms behind views: make a set of concepts connected, rank concepts, closures.

All functions are pure functions of a ``KnowledgeGraph`` so any feature (Studio, Learn, tutor
context, evaluation) can reuse them.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

import networkx as nx

from hoc.core.knowledge import RelationType
from hoc.store.knowledge_graph import KnowledgeGraph

# "related" edges are weak; they connect things too easily, so they are a last resort.
_WEAK = {RelationType.RELATED}


def undirected(graph: KnowledgeGraph, *, weak: bool = False) -> nx.Graph[str]:
    g: nx.Graph[str] = nx.Graph()
    g.add_nodes_from(c.id for c in graph.concepts())
    for r in graph.relations():
        if (weak or r.type not in _WEAK) and r.source in g and r.target in g:
            g.add_edge(r.source, r.target)
    return g


def _components(g: nx.Graph[str], members: set[str]) -> list[set[str]]:
    sub = g.subgraph(members)
    return sorted((set(c) for c in nx.connected_components(sub)), key=lambda c: (-len(c), min(c)))


def _shortest_to(
    g: nx.Graph[str], start: set[str], targets: set[str], max_hops: int
) -> list[str] | None:
    """Shortest path (as node list) from any node in ``start`` to any node in ``targets``."""
    parent: dict[str, str | None] = dict.fromkeys(sorted(start))
    queue = deque((n, 0) for n in sorted(start))
    while queue:
        node, depth = queue.popleft()
        if node in targets:
            path = [node]
            while (p := parent[path[-1]]) is not None:
                path.append(p)
            return path[::-1]
        if depth >= max_hops:
            continue
        for nb in sorted(g.neighbors(node)):
            if nb not in parent:
                parent[nb] = node
                queue.append((nb, depth + 1))
    return None


def connect(
    graph: KnowledgeGraph, concept_ids: Iterable[str], *, max_hops: int = 3
) -> tuple[list[str], list[str], bool]:
    """Connect ``concept_ids`` by adding short paths between their parts.

    Returns ``(members, bridges, connected)``: members are the known input ids (input order),
    bridges the concepts added to connect them, connected whether one piece remains.
    Greedy (nearest part first), so not a minimal Steiner tree, but small and deterministic.
    """
    strong = undirected(graph)
    weak = undirected(graph, weak=True)
    members = list(dict.fromkeys(c for c in concept_ids if c in strong))
    if len(members) < 2:
        return members, [], True
    current = set(members)
    bridges: list[str] = []
    for g in (strong, weak):
        while True:
            parts = _components(g, current)
            if len(parts) <= 1:
                return members, bridges, True
            joined = False
            # grow from the biggest part towards the nearest other part
            others = set().union(*parts[1:])
            path = _shortest_to(g, parts[0], others, max_hops + 1)
            if path:
                for n in path:
                    if n not in current:
                        current.add(n)
                        bridges.append(n)
                joined = True
            if not joined:
                break
    return members, bridges, len(_components(weak, current)) <= 1


def importance(graph: KnowledgeGraph) -> dict[str, float]:
    """How central a concept is: how much depends on it, plus its degree. For overviews."""
    pg = graph.prerequisite_graph()
    deg = undirected(graph, weak=True)
    total = max(1, len(pg))
    return {
        n: len(nx.descendants(pg, n)) / total + 0.5 * deg.degree(n) / max(1, total - 1)
        for n in pg.nodes
    }


def neighbourhood(graph: KnowledgeGraph, concept_id: str, depth: int = 1) -> list[str]:
    g = undirected(graph, weak=True)
    cid = graph.resolve(concept_id)
    if cid not in g:
        return [cid]
    reach = nx.single_source_shortest_path_length(g, cid, cutoff=depth)
    return sorted(reach, key=lambda n: (reach[n], n))


def needed_for(graph: KnowledgeGraph, concept_id: str) -> list[str]:
    """The concept and everything it transitively depends on, in learning order."""
    cid = graph.resolve(concept_id)
    closure = graph.prerequisite_closure([cid])
    try:
        order = graph.learning_order([cid])
    except nx.NetworkXUnfeasible:  # a prerequisite cycle slipped in; still show the closure
        order = sorted(closure)
    return [n for n in order if n in closure]
