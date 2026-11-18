import pytest

from hoc.core.changes import (
    AddMisconception,
    ChangeSetStatus,
    DeprecateConcept,
    LinkMisconception,
    MergeConcepts,
    RemoveRelation,
    RetypeRelation,
    SplitConcept,
    UpdateConceptCard,
    UpdateRelation,
)
from hoc.core.errors import (
    ChangeSetStateError,
    GraphConflictError,
    LockedElementError,
    NotFoundError,
    RevertConflictError,
)
from hoc.core.knowledge import (
    Concept,
    ConceptStatus,
    Misconception,
    Necessity,
    RelationStatus,
    RelationType,
)
from tests.conftest import EXPERT, apply, concept, cs, graph, prereq, seed_basic


def test_add_and_materialise(db, store):
    seed_basic(store)
    g = graph(db)
    assert [c.id for c in g.concepts()] == ["a", "b", "c"]
    assert {r.id for r in g.relations()} == {"prerequisite_of:a->b", "prerequisite_of:b->c"}
    assert all(c.status is ConceptStatus.PROPOSED for c in g.concepts())
    assert g.learning_order() == ["a", "b", "c"]
    assert g.prerequisite_closure(["c"]) == {"a", "b", "c"}


def test_failed_changeset_rolls_back_everything(db, store):
    bad = cs(concept("x"), concept("x"))  # duplicate inside one changeset
    store.propose(bad)
    with pytest.raises(GraphConflictError):
        store.apply(bad.id)
    assert graph(db).concepts() == []
    assert store.get(bad.id).status is ChangeSetStatus.PROPOSED


def test_cycle_is_rejected_and_rolled_back(db, store):
    seed_basic(store)
    cyc = cs(prereq("c", "a"))
    store.propose(cyc)
    with pytest.raises(GraphConflictError, match="cycle"):
        store.apply(cyc.id)
    assert "prerequisite_of:c->a" not in {r.id for r in graph(db).relations(include_removed=True)}


def test_duplicate_active_relation_rejected(store):
    seed_basic(store)
    dup = cs(prereq("a", "b"))
    store.propose(dup)
    with pytest.raises(GraphConflictError, match="already exists"):
        store.apply(dup.id)


def test_relation_needs_active_endpoints(store):
    c = cs(prereq("ghost", "also-ghost"))
    store.propose(c)
    with pytest.raises(NotFoundError):
        store.apply(c.id)


def test_revert_restores_exact_before_state(db, store):
    seed_basic(store)
    before = graph(db)
    merge = apply(store, cs(MergeConcepts(from_id="b", into_id="c")))
    assert graph(db).concept("b").id == "c"
    store.revert(merge.id)
    after = graph(db)
    assert {c.id: c.model_dump() for c in after.concepts(include_inactive=True)} == {
        c.id: c.model_dump() for c in before.concepts(include_inactive=True)
    }
    assert store.get(merge.id).status is ChangeSetStatus.REVERTED


def test_revert_of_creation_deletes_elements(db, store):
    first = apply(store, cs(concept("x"), concept("y"), prereq("x", "y")))
    store.revert(first.id)
    g = graph(db)
    assert g.concepts(include_inactive=True) == []
    assert g.relations(include_removed=True) == []


def test_revert_blocked_by_later_change_to_same_element(store):
    first = apply(store, cs(concept("x")))
    apply(store, cs(UpdateConceptCard(concept_id="x", patch={"one_line": "later"})))
    with pytest.raises(RevertConflictError):
        store.revert(first.id)


def test_revert_allowed_after_later_change_was_reverted(db, store):
    first = apply(store, cs(concept("x")))
    second = apply(store, cs(UpdateConceptCard(concept_id="x", patch={"one_line": "later"})))
    store.revert(second.id)
    store.revert(first.id)
    assert graph(db).concepts(include_inactive=True) == []


def test_merge_rewires_relations_aliases_and_redirects(db, store):
    apply(
        store,
        cs(
            concept("a"),
            concept("b"),
            concept("b2", "Second B"),
            concept("c"),
            prereq("a", "b"),
            prereq("a", "b2"),
            prereq("b", "c"),
            prereq("b2", "c"),
        ),
    )
    apply(store, cs(MergeConcepts(from_id="b2", into_id="b")))
    g = graph(db)
    assert g.resolve("b2") == "b"
    assert "Second B" in g.concept("b").aliases
    active = {r.id for r in g.relations()}
    assert active == {"prerequisite_of:a->b", "prerequisite_of:b->c"}
    merged = {c.id: c for c in g.concepts(include_inactive=True)}["b2"]
    assert merged.status is ConceptStatus.MERGED and merged.redirect_to == "b"
    assert [c.id for c in g.concepts()] == ["a", "b", "c"]


def test_merge_collapses_edge_between_merged_concepts(db, store):
    apply(store, cs(concept("a"), concept("b"), prereq("a", "b")))
    apply(store, cs(MergeConcepts(from_id="a", into_id="b")))
    assert graph(db).relations() == []


def test_merge_into_self_or_inactive_rejected(store):
    apply(store, cs(concept("a"), concept("b")))
    for op in (MergeConcepts(from_id="a", into_id="a"),):
        c = cs(op)
        store.propose(c)
        with pytest.raises(GraphConflictError):
            store.apply(c.id)
    apply(store, cs(DeprecateConcept(concept_id="b")))
    c = cs(MergeConcepts(from_id="a", into_id="b"))
    store.propose(c)
    with pytest.raises(GraphConflictError):
        store.apply(c.id)


def test_concept_ids_are_never_reused_even_after_deprecation(store):
    apply(store, cs(concept("a")))
    apply(store, cs(DeprecateConcept(concept_id="a")))
    again = cs(concept("a"))
    store.propose(again)
    with pytest.raises(GraphConflictError, match="permanent"):
        store.apply(again.id)


def test_split_deprecates_original_and_records_successors(db, store):
    apply(store, cs(concept("big")))
    apply(
        store,
        cs(
            SplitConcept(
                from_id="big",
                into=[Concept(id="part-1", name="p1"), Concept(id="part-2", name="p2")],
            )
        ),
    )
    g = graph(db)
    assert g.concept("big").status is ConceptStatus.DEPRECATED
    assert g.concept("big").split_into == ["part-1", "part-2"]
    assert [c.id for c in g.concepts()] == ["part-1", "part-2"]


def test_deprecate_soft_removes_edges(db, store):
    seed_basic(store)
    apply(store, cs(DeprecateConcept(concept_id="b")))
    assert graph(db).relations() == []
    assert len(graph(db).relations(include_removed=True)) == 2


def test_remove_then_readd_revives_relation(db, store):
    seed_basic(store)
    apply(store, cs(RemoveRelation(relation_id="prerequisite_of:a->b")))
    assert "prerequisite_of:a->b" not in {r.id for r in graph(db).relations()}
    apply(store, cs(prereq("a", "b", Necessity.SUPPORTIVE)))
    rel = {r.id: r for r in graph(db).relations()}["prerequisite_of:a->b"]
    assert rel.necessity is Necessity.SUPPORTIVE
    assert rel.version == 3


def test_update_and_retype_relation(db, store):
    seed_basic(store)
    apply(
        store,
        cs(
            UpdateRelation(
                relation_id="prerequisite_of:a->b",
                patch={"necessity": "supportive", "confidence": 0.9},
            )
        ),
    )
    rel = {r.id: r for r in graph(db).relations()}["prerequisite_of:a->b"]
    assert rel.necessity is Necessity.SUPPORTIVE and rel.confidence == 0.9
    apply(
        store,
        cs(RetypeRelation(relation_id="prerequisite_of:a->b", new_type=RelationType.SPECIALIZES)),
    )
    ids = {r.id for r in graph(db).relations()}
    assert "specializes:a->b" in ids and "prerequisite_of:a->b" not in ids


def test_patch_rejects_disallowed_fields(store):
    apply(store, cs(concept("a")))
    c = cs(UpdateConceptCard(concept_id="a", patch={"not_a_field": 1}))
    store.propose(c)
    with pytest.raises(GraphConflictError):
        store.apply(c.id)


def test_misconception_links_both_ways(db, store):
    apply(store, cs(concept("a"), concept("b")))
    apply(
        store,
        cs(
            AddMisconception(
                misconception=Misconception(id="m1", name="m", statement="s", concept_ids=["a"])
            )
        ),
    )
    apply(store, cs(LinkMisconception(misconception_id="m1", concept_ids=["b"])))
    g = graph(db)
    assert g.concept("a").card.misconception_ids == ["m1"]
    assert g.concept("b").card.misconception_ids == ["m1"]
    assert g.misconceptions()[0].concept_ids == ["a", "b"]


def test_versions_increment(db, store):
    apply(store, cs(concept("a")))
    apply(store, cs(UpdateConceptCard(concept_id="a", patch={"one_line": "x"})))
    assert graph(db).concept("a").version == 2


# ------------------------------------------------------------ approval and locks


def test_approve_proposed_applies_accepts_and_locks(db, store):
    c = cs(concept("a"), concept("b"), prereq("a", "b"))
    store.propose(c)
    done = store.approve(c.id, EXPERT)
    assert done.status is ChangeSetStatus.APPROVED and done.decided_by == EXPERT
    g = graph(db)
    assert all(x.status is ConceptStatus.ACCEPTED and x.locked for x in g.concepts())
    assert all(r.status is RelationStatus.ACCEPTED and r.locked for r in g.relations())


def test_approve_auto_applied_promotes_in_place(db, store):
    c = apply(store, cs(concept("a")))
    store.approve(c.id, EXPERT)
    a = graph(db).concept("a")
    assert a.status is ConceptStatus.ACCEPTED and a.locked and a.version == 1
    # still revertible to the pre-change state
    store.revert(c.id)
    assert graph(db).concepts(include_inactive=True) == []


def test_agent_cannot_modify_locked_elements_but_expert_approval_can(db, store):
    c = cs(concept("a"))
    store.propose(c)
    store.approve(c.id, EXPERT)

    sneaky = cs(UpdateConceptCard(concept_id="a", patch={"one_line": "agent edit"}))
    store.propose(sneaky)
    with pytest.raises(LockedElementError):
        store.apply(sneaky.id)
    assert graph(db).concept("a").card.one_line == ""

    store.approve(sneaky.id, EXPERT)
    assert graph(db).concept("a").card.one_line == "agent edit"


def test_reject_and_state_machine(store):
    c = cs(concept("a"))
    store.propose(c)
    store.reject(c.id, EXPERT)
    with pytest.raises(ChangeSetStateError):
        store.apply(c.id)
    with pytest.raises(ChangeSetStateError):
        store.revert(c.id)
    with pytest.raises(ChangeSetStateError):
        store.reject(c.id, EXPERT)  # already rejected
    with pytest.raises(ChangeSetStateError):
        store.propose(c)  # duplicate id


def test_cannot_apply_twice(store):
    c = apply(store, cs(concept("a")))
    with pytest.raises(ChangeSetStateError):
        store.apply(c.id)


def test_applied_seq_is_monotonic(store):
    a = apply(store, cs(concept("a")))
    b = apply(store, cs(concept("b")))
    assert a.applied_seq == 1 and b.applied_seq == 2
