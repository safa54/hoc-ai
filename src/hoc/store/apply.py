"""Semantics of each GraphOp. One small function per op, registered in ``_APPLIERS``.

Rules every applier follows:
  * validate preconditions and raise GraphConflictError / NotFoundError with a clear message;
  * write only through ``GraphTx`` (which records before-states and enforces locks);
  * never delete: removal is a status change so history and evidence links stay valid.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import networkx as nx

from hoc.core.changes import (
    AddConcept,
    AddMisconception,
    AddRelation,
    ChangeSet,
    DeprecateConcept,
    GraphOp,
    LinkMisconception,
    MergeConcepts,
    RemoveRelation,
    RetypeRelation,
    SplitConcept,
    UpdateConcept,
    UpdateConceptCard,
    UpdateRelation,
)
from hoc.core.common import Actor
from hoc.core.errors import GraphConflictError, NotFoundError
from hoc.core.knowledge import (
    EDGE_REGISTRY,
    Concept,
    ConceptCard,
    ConceptStatus,
    Relation,
    RelationStatus,
)
from hoc.store.graph_tx import GraphTx


@dataclass(frozen=True)
class ApplyContext:
    author: Actor


# ------------------------------------------------------------------------ helpers


def _active_concept(tx: GraphTx, id_: str) -> Concept:
    concept = tx.get_concept(id_)
    if concept is None:
        raise NotFoundError(f"concept {id_!r} does not exist")
    if not concept.status.is_active:
        raise GraphConflictError(f"concept {id_!r} is {concept.status.value}, not active")
    return concept


def _active_relation(tx: GraphTx, id_: str) -> Relation:
    rel = tx.get_relation(id_)
    if rel is None or not rel.status.is_active:
        raise NotFoundError(f"active relation {id_!r} does not exist")
    return rel


def _clone_relation(rel: Relation, **changes: Any) -> Relation:
    """Re-validate a relation with changes; id is re-derived from (type, source, target)."""
    data = rel.model_dump()
    data.update(changes)
    data["id"] = ""
    return Relation.model_validate(data)


def _put_new_relation(tx: GraphTx, rel: Relation) -> None:
    """Create a relation, or revive a soft-removed one. Active duplicates are an error."""
    _active_concept(tx, rel.source)
    _active_concept(tx, rel.target)
    existing = tx.get_relation(rel.id)
    if existing is not None and existing.status.is_active:
        raise GraphConflictError(f"relation {rel.id!r} already exists; use update_relation")
    tx.put_relation(rel)


def _remove(tx: GraphTx, rel: Relation) -> None:
    tx.put_relation(rel.model_copy(update={"status": RelationStatus.REMOVED}))


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _patched(
    model_dump: dict[str, Any], patch: dict[str, Any], allowed: set[str]
) -> dict[str, Any]:
    bad = set(patch) - allowed
    if bad:
        raise GraphConflictError(f"cannot patch {sorted(bad)}; allowed: {sorted(allowed)}")
    return {**model_dump, **patch}


# ---------------------------------------------------------------------- concept ops


def _add_concept(tx: GraphTx, op: AddConcept, ctx: ApplyContext) -> None:
    if tx.get_concept(op.concept.id) is not None:
        raise GraphConflictError(f"concept id {op.concept.id!r} already used (ids are permanent)")
    tx.put_concept(
        op.concept.model_copy(
            update={
                "status": ConceptStatus.PROPOSED,
                "locked": False,
                "version": 1,
                "redirect_to": None,
                "split_into": [],
                "created_by": op.concept.created_by or ctx.author,
            }
        )
    )


def _update_concept(tx: GraphTx, op: UpdateConcept, ctx: ApplyContext) -> None:
    c = _active_concept(tx, op.concept_id)
    data = _patched(c.model_dump(), op.patch, {"name", "aliases", "kind", "topic_ids"})
    tx.put_concept(Concept.model_validate(data))


def _update_card(tx: GraphTx, op: UpdateConceptCard, ctx: ApplyContext) -> None:
    c = _active_concept(tx, op.concept_id)
    card = ConceptCard.model_validate(
        _patched(c.card.model_dump(), op.patch, set(ConceptCard.model_fields))
    )
    tx.put_concept(c.model_copy(update={"card": card}))


def _merge(tx: GraphTx, op: MergeConcepts, ctx: ApplyContext) -> None:
    if op.from_id == op.into_id:
        raise GraphConflictError("cannot merge a concept into itself")
    src = _active_concept(tx, op.from_id)
    dst = _active_concept(tx, op.into_id)

    card = dst.card.model_copy(
        update={
            "source_refs": _dedupe([*dst.card.source_refs, *src.card.source_refs]),
            "misconception_ids": _dedupe(
                [*dst.card.misconception_ids, *src.card.misconception_ids]
            ),
        }
    )
    tx.put_concept(
        dst.model_copy(
            update={
                "aliases": _dedupe([*dst.aliases, src.name, *src.aliases]),
                "topic_ids": _dedupe([*dst.topic_ids, *src.topic_ids]),
                "card": card,
            }
        )
    )
    tx.put_concept(src.model_copy(update={"status": ConceptStatus.MERGED, "redirect_to": dst.id}))

    for rel in tx.relations_of(src.id):
        new_source = dst.id if rel.source == src.id else rel.source
        new_target = dst.id if rel.target == src.id else rel.target
        _remove(tx, rel)
        if new_source == new_target:  # edge between the two merged concepts collapses
            continue
        moved = _clone_relation(rel, source=new_source, target=new_target)
        existing = tx.get_relation(moved.id)
        if existing is not None and existing.status.is_active:
            best = max(existing.confidence, rel.confidence)
            tx.put_relation(existing.model_copy(update={"confidence": best}))
        else:
            tx.put_relation(moved)

    for m in tx.all_misconceptions():
        if src.id in m.concept_ids:
            ids = _dedupe([dst.id if x == src.id else x for x in m.concept_ids])
            tx.put_misconception(m.model_copy(update={"concept_ids": ids}))


def _split(tx: GraphTx, op: SplitConcept, ctx: ApplyContext) -> None:
    src = _active_concept(tx, op.from_id)
    for part in op.into:
        _add_concept(tx, AddConcept(concept=part), ctx)
    tx.put_concept(
        src.model_copy(
            update={
                "status": ConceptStatus.DEPRECATED,
                "split_into": [p.id for p in op.into],
            }
        )
    )


def _deprecate(tx: GraphTx, op: DeprecateConcept, ctx: ApplyContext) -> None:
    c = _active_concept(tx, op.concept_id)
    for rel in tx.relations_of(c.id):
        _remove(tx, rel)
    tx.put_concept(c.model_copy(update={"status": ConceptStatus.DEPRECATED}))


# --------------------------------------------------------------------- relation ops


def _add_relation(tx: GraphTx, op: AddRelation, ctx: ApplyContext) -> None:
    rel = op.relation.model_copy(
        update={
            "status": RelationStatus.PROPOSED,
            "locked": False,
            "version": 1,
            "created_by": op.relation.created_by or ctx.author,
        }
    )
    _put_new_relation(tx, rel)


def _update_relation(tx: GraphTx, op: UpdateRelation, ctx: ApplyContext) -> None:
    rel = _active_relation(tx, op.relation_id)
    allowed = {"necessity", "rationale", "confidence", "scope", "source_refs", "evidence_summary"}
    data = _patched(rel.model_dump(), op.patch, allowed)
    new = Relation.model_validate({**data, "id": ""})
    tx.put_relation(new)


def _retype(tx: GraphTx, op: RetypeRelation, ctx: ApplyContext) -> None:
    rel = _active_relation(tx, op.relation_id)
    new = _clone_relation(
        rel,
        type=op.new_type,
        necessity=op.necessity,
        status=RelationStatus.PROPOSED,
        locked=False,
        version=1,
    )
    _remove(tx, rel)
    _put_new_relation(tx, new)


def _remove_relation(tx: GraphTx, op: RemoveRelation, ctx: ApplyContext) -> None:
    _remove(tx, _active_relation(tx, op.relation_id))


# ---------------------------------------------------------------- misconception ops


def _add_misconception(tx: GraphTx, op: AddMisconception, ctx: ApplyContext) -> None:
    m = op.misconception
    if tx.get_misconception(m.id) is not None:
        raise GraphConflictError(f"misconception {m.id!r} already exists")
    for cid in m.concept_ids:
        _active_concept(tx, cid)
    tx.put_misconception(
        m.model_copy(update={"version": 1, "created_by": m.created_by or ctx.author})
    )
    for cid in m.concept_ids:
        _link_concept_back(tx, cid, m.id)


def _link_concept_back(tx: GraphTx, concept_id: str, misconception_id: str) -> None:
    c = _active_concept(tx, concept_id)
    if misconception_id not in c.card.misconception_ids:
        ids = [*c.card.misconception_ids, misconception_id]
        tx.put_concept(
            c.model_copy(update={"card": c.card.model_copy(update={"misconception_ids": ids})})
        )


def _link_misconception(tx: GraphTx, op: LinkMisconception, ctx: ApplyContext) -> None:
    m = tx.get_misconception(op.misconception_id)
    if m is None:
        raise NotFoundError(f"misconception {op.misconception_id!r} does not exist")
    for cid in op.concept_ids:
        _active_concept(tx, cid)
    for rid in op.relation_ids:
        _active_relation(tx, rid)
    tx.put_misconception(
        m.model_copy(
            update={
                "concept_ids": _dedupe([*m.concept_ids, *op.concept_ids]),
                "relation_ids": _dedupe([*m.relation_ids, *op.relation_ids]),
            }
        )
    )
    for cid in op.concept_ids:
        _link_concept_back(tx, cid, m.id)


# ------------------------------------------------------------------------ dispatch

_Applier = Callable[[GraphTx, Any, ApplyContext], None]
_APPLIERS: dict[type, _Applier] = {
    AddConcept: _add_concept,
    UpdateConcept: _update_concept,
    UpdateConceptCard: _update_card,
    MergeConcepts: _merge,
    SplitConcept: _split,
    DeprecateConcept: _deprecate,
    AddRelation: _add_relation,
    UpdateRelation: _update_relation,
    RetypeRelation: _retype,
    RemoveRelation: _remove_relation,
    AddMisconception: _add_misconception,
    LinkMisconception: _link_misconception,
}


def apply_op(tx: GraphTx, op: GraphOp, ctx: ApplyContext) -> None:
    applier = _APPLIERS.get(type(op))
    if applier is None:  # pragma: no cover - guarded by the union type
        raise GraphConflictError(f"no applier for {type(op).__name__}")
    applier(tx, op, ctx)


def apply_changeset_ops(tx: GraphTx, cs: ChangeSet) -> None:
    ctx = ApplyContext(author=cs.author)
    for op in cs.ops:
        apply_op(tx, op, ctx)


# --------------------------------------------------------------------- invariants


def check_invariants(tx: GraphTx) -> None:
    """Graph-wide rules that must hold after every ChangeSet."""
    for type_, spec in EDGE_REGISTRY.items():
        if not spec.acyclic:
            continue
        g: nx.DiGraph[str] = nx.DiGraph()
        g.add_edges_from((r.source, r.target) for r in tx.active_relations({type_.value}))
        if not nx.is_directed_acyclic_graph(g):
            cycle = " -> ".join(str(u) for u, _ in nx.find_cycle(g))
            raise GraphConflictError(f"{type_.value} would form a cycle: {cycle}")
