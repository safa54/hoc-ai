from hoc.construct.drafts import ConceptDraft, DraftOp, compile_ops
from hoc.core.changes import AddConcept, AddRelation, RemoveRelation, SplitConcept
from hoc.store import KnowledgeGraph
from tests.conftest import apply, concept, cs, prereq


def _graph(store, db):
    apply(store, cs(concept("a"), concept("b"), prereq("a", "b")))
    return KnowledgeGraph.load(db)


def test_add_concept_and_relation_resolve_names(store, db):
    g = _graph(store, db)
    out = compile_ops(
        [
            DraftOp(op="add_concept", concepts=[ConceptDraft(name="New Idea", summary="s")]),
            DraftOp(
                op="add_relation",
                relation_type="prerequisite_of",
                source="B",
                target="New Idea",
                necessity="supportive",
            ),
        ],
        g,
        topic_id="area-1",
    )
    assert out.problems == []
    add, rel = out.ops
    assert isinstance(add, AddConcept) and add.concept.id == "new-idea"
    assert add.concept.topic_ids == ["area-1"]
    assert isinstance(rel, AddRelation) and (rel.relation.source, rel.relation.target) == (
        "b",
        "new-idea",
    )


def test_bad_ops_are_reported_not_raised(store, db):
    g = _graph(store, db)
    out = compile_ops(
        [
            DraftOp(op="add_concept", concepts=[ConceptDraft(id="a", name="A again")]),
            DraftOp(op="add_relation", relation_type="related", source="a", target="ghost"),
            DraftOp(op="update_concept", concept_id="a", concepts=[ConceptDraft()]),
            DraftOp(op="split_concept", concept_id="a", concepts=[ConceptDraft(name="Only")]),
        ],
        g,
    )
    assert out.ops == [] and len(out.problems) == 4


def test_split_retires_old_relations(store, db):
    g = _graph(store, db)
    out = compile_ops(
        [
            DraftOp(
                op="split_concept",
                concept_id="b",
                concepts=[ConceptDraft(name="B one"), ConceptDraft(name="B two")],
            )
        ],
        g,
    )
    assert isinstance(out.ops[0], SplitConcept)
    assert any(isinstance(o, RemoveRelation) for o in out.ops[1:])


def test_ops_compose_within_one_proposal(store, db):
    """A concept added earlier in the same proposal can be referenced by later ops."""
    g = _graph(store, db)
    out = compile_ops(
        [
            DraftOp(op="add_concept", concepts=[ConceptDraft(name="Fresh")]),
            DraftOp(op="add_relation", relation_type="specializes", source="fresh", target="a"),
        ],
        g,
    )
    assert out.problems == [] and len(out.ops) == 2


def test_move_concept_to_an_area(store, db):
    g = _graph(store, db)
    out = compile_ops([DraftOp(op="update_concept", concept_id="a", area="Area Two")], g)
    assert out.problems == [] and len(out.ops) == 1
    assert out.ops[0].patch == {"topic_ids": ["area-two"]}
    assert compile_ops([DraftOp(op="update_concept", concept_id="a")], g).problems  # nothing given
