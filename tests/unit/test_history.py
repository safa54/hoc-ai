from hoc.core.changes import UpdateConceptCard
from hoc.store.history import graph_at
from tests.conftest import apply, concept, cs, prereq


def test_diff_and_graph_at(store, db):
    first = apply(store, cs(concept("a"), concept("b"), prereq("a", "b")))
    second = apply(
        store, cs(UpdateConceptCard(concept_id="a", patch={"one_line": "hello"}), concept("c"))
    )
    diff = store.diff(second.id)
    kinds = {(c.id, c.change) for c in diff.changes}
    assert kinds == {("a", "updated"), ("c", "added")}
    assert next(c for c in diff.changes if c.id == "a").detail == "card.one_line"

    assert {c.id for c in graph_at(db, 0).concepts()} == set()
    mid = graph_at(db, first.applied_seq or 0)
    assert {c.id for c in mid.concepts()} == {"a", "b"} and len(mid.relations()) == 1
    assert mid.concept("a").card.one_line == ""
    assert {c.id for c in graph_at(db, second.applied_seq or 0).concepts()} == {"a", "b", "c"}


def test_preview_writes_nothing_and_reports_bad_ops(store, db):
    apply(store, cs(concept("a")))
    proposal = cs(concept("b"), prereq("a", "ghost"), concept("a"))
    result = store.preview(proposal)
    assert [i for i, _ in result.errors] == [1, 2]
    assert [c.id for c in result.diff.changes if c.change == "added"] == ["b"]
    from hoc.store import KnowledgeGraph

    assert {c.id for c in KnowledgeGraph.load(db).concepts()} == {"a"}
