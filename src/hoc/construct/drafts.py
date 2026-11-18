"""Draft operations: the small, flat vocabulary agents (and the UI) use to describe changes.

``GraphOp`` (hoc.core.changes) is the precise, storage-level language. It is too detailed to
ask a model for directly, so agents write ``DraftOp``s and ``compile_ops`` translates them
into GraphOps against the current graph. The schema is deliberately flat, with no optional
or union types, so every structured-output implementation can produce it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from hoc.core.changes import (
    AddConcept,
    AddRelation,
    DeprecateConcept,
    GraphOp,
    MergeConcepts,
    RemoveRelation,
    SplitConcept,
    UpdateConcept,
    UpdateConceptCard,
    UpdateRelation,
)
from hoc.core.knowledge import (
    EDGE_REGISTRY,
    Concept,
    ConceptCard,
    ConceptKind,
    KeyIdea,
    Necessity,
    Relation,
    RelationType,
)
from hoc.store.knowledge_graph import KnowledgeGraph

OpName = Literal[
    "add_concept",
    "update_concept",
    "split_concept",
    "merge_concepts",
    "remove_concept",
    "add_relation",
    "update_relation",
    "remove_relation",
]


class ConceptDraft(BaseModel):
    id: str = Field(default="", description="lower-case slug, e.g. euclidean-algorithm")
    name: str = ""
    kind: Literal["concept", "principle", "procedure"] = "concept"
    summary: str = Field(default="", description="one sentence: what it is")
    definition: str = ""
    key_ideas: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class DraftOp(BaseModel):
    op: OpName
    concept_id: str = Field(default="", description="existing concept this op acts on")
    into_id: str = Field(default="", description="merge_concepts: the concept that survives")
    concepts: list[ConceptDraft] = Field(default_factory=list)
    relation_type: Literal[
        "", "prerequisite_of", "specializes", "part_of", "contrasts_with", "analogous_to", "related"
    ] = ""
    source: str = ""
    target: str = ""
    necessity: Literal["", "essential", "supportive"] = ""
    rationale: str = ""
    area: str = Field(default="", description="update_concept: move the concept to this area id")


OPS_GUIDE = """\
Each op is one object. Fill the fields the op needs; leave the others empty ("" or []).

- add_concept: `concepts` = one or more new concepts (id, name, kind, summary, definition,
  key_ideas, examples). Ids are lower-case slugs and must not exist yet.
- update_concept: `concept_id` + `concepts` = exactly one entry holding the new content
  (or no entry and `area` = an area id, to move the concept to that area).
  Empty fields are left unchanged; a non-empty list replaces the old list.
- split_concept: `concept_id` (too coarse) + `concepts` = two or more finer concepts that
  replace it. The old concept and all its relations are retired, so add the relations of the
  new parts with add_relation ops in the same proposal.
- merge_concepts: `concept_id` is absorbed into `into_id`; its relations move over.
- remove_concept: `concept_id` is retired together with its relations.
- add_relation: `relation_type`, `source`, `target`, `rationale`.
  For prerequisite_of, source is needed to understand target, and `necessity` is
  "essential" (target cannot be understood without source) or "supportive" (source helps).
  Other types: specializes (source is a special case of target), part_of (source is a
  component of target), contrasts_with, analogous_to, related (avoid; weak catch-all).
- update_relation: same identifying fields; changes `necessity` and/or `rationale`.
- remove_relation: `relation_type`, `source`, `target`.

Prerequisites must stay acyclic. Do not add a prerequisite that already follows from a chain
(if a -> b and b -> c exist, do not add a -> c)."""


@dataclass
class Compiled:
    ops: list[GraphOp] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    from hoc.core.common import slugify

    try:
        return slugify(text)
    except ValueError:
        return ""


def _to_concept(d: ConceptDraft, concept_id: str, topic_id: str | None = None) -> Concept:
    return Concept(
        id=concept_id,
        topic_ids=[topic_id] if topic_id else [],
        name=d.name.strip() or concept_id.replace("-", " ").capitalize(),
        kind=ConceptKind(d.kind),
        card=ConceptCard(
            one_line=d.summary.strip(),
            definition=d.definition.strip(),
            key_ideas=_key_ideas(d.key_ideas),
            examples=[e for e in d.examples if e.strip()],
        ),
    )


def _key_ideas(texts: list[str]) -> list[KeyIdea]:
    return [KeyIdea(id=f"KI{i}", text=t.strip()) for i, t in enumerate(texts, 1) if t.strip()]


class _Compiler:
    def __init__(self, graph: KnowledgeGraph, topic_id: str | None) -> None:
        self.graph = graph
        self.topic_id = topic_id
        self.active = {c.id for c in graph.concepts()}
        self.taken = {c.id for c in graph.concepts(include_inactive=True)}
        self.out = Compiled()

    def ref(self, raw: str) -> str | None:
        """Resolve what a model wrote (id, sloppy id, or name) to an active concept id."""
        for candidate in (raw.strip(), _slug(raw)):
            if candidate in self.active:
                return candidate
        return None

    def problem(self, index: int, d: DraftOp, message: str) -> None:
        self.out.problems.append(f"op {index + 1} ({d.op}): {message}")

    def new_concepts(self, index: int, d: DraftOp) -> list[Concept]:
        made: list[Concept] = []
        for draft in d.concepts:
            cid = _slug(draft.id or draft.name)
            if not cid:
                self.problem(index, d, "concept without id or name")
            elif cid in self.taken:
                self.problem(index, d, f"concept id {cid!r} already exists")
            else:
                self.taken.add(cid)
                made.append(_to_concept(draft, cid, self.topic_id))
        return made

    def relation(self, index: int, d: DraftOp) -> Relation | None:
        source, target = self.ref(d.source), self.ref(d.target)
        if not d.relation_type:
            self.problem(index, d, "missing relation_type")
            return None
        if source is None or target is None:
            missing = d.source if source is None else d.target
            self.problem(index, d, f"unknown concept {missing!r}")
            return None
        if source == target:
            self.problem(index, d, "a concept cannot relate to itself")
            return None
        type_ = RelationType(d.relation_type)
        necessity: Necessity | None = None
        if EDGE_REGISTRY[type_].needs_necessity:
            necessity = Necessity(d.necessity or "essential")
        return Relation(
            type=type_,
            source=source,
            target=target,
            necessity=necessity,
            rationale=d.rationale.strip(),
        )

    # ------------------------------------------------------------------ per op

    def add_concept(self, i: int, d: DraftOp) -> None:
        if not d.concepts:
            return self.problem(i, d, "no concepts given")
        for concept in self.new_concepts(i, d):
            self.active.add(concept.id)
            self.out.ops.append(AddConcept(concept=concept, note=d.rationale))

    def update_concept(self, i: int, d: DraftOp) -> None:
        cid = self.ref(d.concept_id)
        if cid is None:
            return self.problem(i, d, f"unknown concept {d.concept_id!r}")
        if len(d.concepts) > 1 or (not d.concepts and not d.area.strip()):
            return self.problem(i, d, "needs exactly one entry in `concepts` (or an `area`)")
        new = d.concepts[0] if d.concepts else ConceptDraft()
        old = self.graph.concept(cid) if cid in {c.id for c in self.graph.concepts()} else None
        top: dict[str, Any] = {}
        if new.name.strip() and (old is None or new.name.strip() != old.name):
            top["name"] = new.name.strip()
        if old is not None and new.kind != old.kind.value and (new.summary or new.definition):
            top["kind"] = new.kind
        if d.area.strip():
            top["topic_ids"] = [_slug(d.area)]
        card: dict[str, Any] = {}
        if new.summary.strip():
            card["one_line"] = new.summary.strip()
        if new.definition.strip():
            card["definition"] = new.definition.strip()
        if new.key_ideas:
            card["key_ideas"] = [k.model_dump() for k in _key_ideas(new.key_ideas)]
        if new.examples:
            card["examples"] = new.examples
        if not top and not card:
            return self.problem(i, d, "nothing to change")
        if top:
            self.out.ops.append(UpdateConcept(concept_id=cid, patch=top, note=d.rationale))
        if card:
            self.out.ops.append(UpdateConceptCard(concept_id=cid, patch=card, note=d.rationale))

    def split_concept(self, i: int, d: DraftOp) -> None:
        cid = self.ref(d.concept_id)
        if cid is None:
            return self.problem(i, d, f"unknown concept {d.concept_id!r}")
        parts = self.new_concepts(i, d)
        if len(parts) < 2:
            return self.problem(i, d, "a split needs at least two new concepts")
        self.out.ops.append(SplitConcept(from_id=cid, into=parts, note=d.rationale))
        try:
            old_relations = self.graph.relations_of(cid)
        except Exception:  # concept was added earlier in this same proposal
            old_relations = []
        for rel in old_relations:
            self.out.ops.append(RemoveRelation(relation_id=rel.id, note=f"{cid} was split"))
        self.active.discard(cid)
        self.active.update(p.id for p in parts)

    def merge_concepts(self, i: int, d: DraftOp) -> None:
        src, dst = self.ref(d.concept_id), self.ref(d.into_id)
        if src is None or dst is None or src == dst:
            return self.problem(i, d, "needs two different existing concepts")
        self.out.ops.append(MergeConcepts(from_id=src, into_id=dst, note=d.rationale))
        self.active.discard(src)

    def remove_concept(self, i: int, d: DraftOp) -> None:
        cid = self.ref(d.concept_id)
        if cid is None:
            return self.problem(i, d, f"unknown concept {d.concept_id!r}")
        self.out.ops.append(DeprecateConcept(concept_id=cid, note=d.rationale))
        self.active.discard(cid)

    def add_relation(self, i: int, d: DraftOp) -> None:
        rel = self.relation(i, d)
        if rel is not None:
            self.out.ops.append(AddRelation(relation=rel, note=d.rationale))

    def update_relation(self, i: int, d: DraftOp) -> None:
        rel = self.relation(i, d)
        if rel is None:
            return
        patch: dict[str, Any] = {}
        if d.necessity and rel.necessity is not None:
            patch["necessity"] = d.necessity
        if d.rationale.strip():
            patch["rationale"] = d.rationale.strip()
        if not patch:
            return self.problem(i, d, "nothing to change")
        self.out.ops.append(UpdateRelation(relation_id=rel.id, patch=patch))

    def remove_relation(self, i: int, d: DraftOp) -> None:
        rel = self.relation(i, d)
        if rel is not None:
            self.out.ops.append(RemoveRelation(relation_id=rel.id, note=d.rationale))


def compile_ops(
    drafts: list[DraftOp], graph: KnowledgeGraph, topic_id: str | None = None
) -> Compiled:
    """Translate draft ops into GraphOps. Unusable drafts are reported, not raised, so one
    bad op from a model never sinks the rest of a proposal."""
    compiler = _Compiler(graph, topic_id)
    for index, draft in enumerate(drafts):
        getattr(compiler, draft.op)(index, draft)
    return compiler.out
