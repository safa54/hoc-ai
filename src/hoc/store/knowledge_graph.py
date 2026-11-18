"""KnowledgeGraph: an immutable, in-memory read model of the materialised graph.

Everything that *reads* the graph (policy, UI, validators, evaluation) uses this class, so
the write path (GraphTx) stays tiny and storage details never leak upward.
"""

from __future__ import annotations

from collections.abc import Iterable

import networkx as nx

from hoc.core.errors import NotFoundError
from hoc.core.knowledge import (
    Concept,
    Misconception,
    Necessity,
    Relation,
    RelationType,
)
from hoc.store.db import Database


class KnowledgeGraph:
    def __init__(
        self,
        concepts: Iterable[Concept],
        relations: Iterable[Relation],
        misconceptions: Iterable[Misconception] = (),
    ) -> None:
        self._concepts = {c.id: c for c in concepts}
        self._relations = {r.id: r for r in relations}
        self._misconceptions = {m.id: m for m in misconceptions}

    # ------------------------------------------------------------- loading

    @classmethod
    def load(cls, db: Database) -> KnowledgeGraph:
        with db.read() as conn:
            concepts = [
                Concept.model_validate_json(r["data"])
                for r in conn.execute("SELECT data FROM concepts")
            ]
            relations = [
                Relation.model_validate_json(r["data"])
                for r in conn.execute("SELECT data FROM relations")
            ]
            miscs = [
                Misconception.model_validate_json(r["data"])
                for r in conn.execute("SELECT data FROM misconceptions")
            ]
        return cls(concepts, relations, miscs)

    # ------------------------------------------------------------- concepts

    def resolve(self, concept_id: str) -> str:
        """Follow merge redirects to the surviving concept id."""
        seen: set[str] = set()
        current = concept_id
        while True:
            c = self._concepts.get(current)
            if c is None:
                raise NotFoundError(f"concept {concept_id!r} not found")
            if c.redirect_to is None:
                return current
            if current in seen:  # pragma: no cover - guarded by merge rules
                raise ValueError(f"redirect cycle at {current!r}")
            seen.add(current)
            current = c.redirect_to

    def concept(self, concept_id: str) -> Concept:
        return self._concepts[self.resolve(concept_id)]

    def concepts(self, *, include_inactive: bool = False) -> list[Concept]:
        items = self._concepts.values()
        return sorted(
            (c for c in items if include_inactive or c.status.is_active), key=lambda c: c.id
        )

    # ------------------------------------------------------------ relations

    def relations(
        self, type_: RelationType | None = None, *, include_removed: bool = False
    ) -> list[Relation]:
        return sorted(
            (
                r
                for r in self._relations.values()
                if (include_removed or r.status.is_active) and (type_ is None or r.type is type_)
            ),
            key=lambda r: r.id,
        )

    def relations_of(self, concept_id: str) -> list[Relation]:
        cid = self.resolve(concept_id)
        return [r for r in self.relations() if cid in (r.source, r.target)]

    def misconceptions(self) -> list[Misconception]:
        return sorted(self._misconceptions.values(), key=lambda m: m.id)

    # ----------------------------------------------------------- algorithms

    def prerequisite_graph(self, *, essential_only: bool = False) -> nx.DiGraph[str]:
        """Edges point prerequisite -> dependent."""
        g: nx.DiGraph[str] = nx.DiGraph()
        g.add_nodes_from(c.id for c in self.concepts())
        for r in self.relations(RelationType.PREREQUISITE_OF):
            if essential_only and r.necessity is not Necessity.ESSENTIAL:
                continue
            g.add_edge(r.source, r.target)
        return g

    def prerequisite_closure(
        self, concept_ids: Iterable[str], *, essential_only: bool = False
    ) -> set[str]:
        """The targets plus everything they transitively depend on."""
        g = self.prerequisite_graph(essential_only=essential_only)
        result: set[str] = set()
        for cid in concept_ids:
            rid = self.resolve(cid)
            result.add(rid)
            result |= nx.ancestors(g, rid)
        return result

    def learning_order(self, concept_ids: Iterable[str] | None = None) -> list[str]:
        """A topological order of the prerequisite graph (deterministic)."""
        g = self.prerequisite_graph()
        if concept_ids is not None:
            g = g.subgraph(self.prerequisite_closure(concept_ids)).copy()
        return list(nx.lexicographical_topological_sort(g))
