"""Pure functions turning a KnowledgeGraph into display formats (no Streamlit imports)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass

import networkx as nx

from hoc.core.knowledge import ConceptStatus, Necessity, RelationType
from hoc.store.knowledge_graph import KnowledgeGraph

_EDGE_STYLE: dict[RelationType, str] = {
    RelationType.PREREQUISITE_OF: 'color="#374151"',
    RelationType.SPECIALIZES: 'color="#2563eb", arrowhead=empty',
    RelationType.PART_OF: 'color="#7c3aed", arrowhead=odiamond',
    RelationType.CONTRASTS_WITH: 'color="#dc2626", dir=both, style=dotted',
    RelationType.ANALOGOUS_TO: 'color="#059669", dir=both, style=dotted',
    RelationType.RELATED: 'color="#9ca3af", dir=none, style=dotted',
}


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def graph_to_dot(
    graph: KnowledgeGraph, relation_types: Collection[RelationType] | None = None
) -> str:
    """Graphviz DOT. Proposed = light fill, accepted = solid, locked = bold border."""
    types = set(relation_types) if relation_types is not None else set(RelationType)
    lines = [
        "digraph G {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fontsize=11];',
    ]
    for c in graph.concepts():
        fill = "#dbeafe" if c.status is ConceptStatus.ACCEPTED else "#f3f4f6"
        pen = 3 if c.locked else 1
        lines.append(
            f'  "{_esc(c.id)}" [label="{_esc(c.name)}", fillcolor="{fill}", penwidth={pen}];'
        )
    for r in graph.relations():
        if r.type not in types:
            continue
        style = _EDGE_STYLE[r.type]
        if r.necessity is Necessity.SUPPORTIVE:
            style += ", style=dashed"
        lines.append(f'  "{_esc(r.source)}" -> "{_esc(r.target)}" [{style}];')
    lines.append("}")
    return "\n".join(lines)


@dataclass(frozen=True)
class LayoutNode:
    id: str
    label: str
    x: float
    y: float
    accepted: bool
    locked: bool


@dataclass(frozen=True)
class LayoutEdge:
    id: str
    type: RelationType
    source: str
    target: str
    necessity: Necessity | None
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class GraphLayout:
    nodes: list[LayoutNode]
    edges: list[LayoutEdge]


def graph_layout(
    graph: KnowledgeGraph, relation_types: Collection[RelationType] | None = None
) -> GraphLayout:
    """Deterministic layered layout (prerequisite depth -> x) with plain coordinates.

    Front ends draw this however they like; ids are the stable concept/relation ids, so a
    click on a node or an edge can be mapped straight back to the graph.
    """
    types = set(relation_types) if relation_types is not None else set(RelationType)
    prereq = graph.prerequisite_graph()
    depth: dict[str, int] = {}
    for level, generation in enumerate(nx.topological_generations(prereq)):
        for cid in generation:
            depth[cid] = level
    columns: dict[int, list[str]] = defaultdict(list)
    for c in graph.concepts():
        columns[depth.get(c.id, 0)].append(c.id)

    pos: dict[str, tuple[float, float]] = {}
    for level, ids in columns.items():
        n = len(ids)
        for i, cid in enumerate(sorted(ids)):
            pos[cid] = (float(level), (n - 1) / 2 - i)

    nodes = [
        LayoutNode(c.id, c.name, *pos[c.id], c.status is ConceptStatus.ACCEPTED, bool(c.locked))
        for c in graph.concepts()
    ]
    edges = [
        LayoutEdge(r.id, r.type, r.source, r.target, r.necessity, *pos[r.source], *pos[r.target])
        for r in graph.relations()
        if r.type in types and r.source in pos and r.target in pos
    ]
    return GraphLayout(nodes, edges)
