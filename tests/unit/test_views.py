"""Graph views / lenses (ADR 0012): connecting, providers, saved views, read-only guarantee."""

import pytest

from hoc.config import ViewSettings
from hoc.core import build as b
from hoc.core.build import Area, GraphTest, TraceStep
from hoc.core.changes import AddConcept, AddRelation
from hoc.core.common import Actor
from hoc.core.errors import HocError
from hoc.core.knowledge import Concept, Relation, RelationType
from hoc.core.views import SavedView, ViewKind
from hoc.services.views import ViewService
from hoc.views import (
    VIEW_PROVIDERS,
    ViewInputs,
    compute_views,
    connect,
    needed_for,
    needed_for_view,
    register_view_provider,
)
from tests.conftest import apply, cs, graph, prereq
from tests.unit.test_construct import run_until_idle


def area_concept(cid: str, area: str) -> AddConcept:
    return AddConcept(concept=Concept(id=cid, name=cid.upper(), topic_ids=[area]))


def related(a: str, b: str) -> AddRelation:
    return AddRelation(relation=Relation(type=RelationType.RELATED, source=a, target=b))


@pytest.fixture
def chain(db, store):
    """a -> b -> c -> d (prerequisites) in two areas, plus x related to d, and lonely z."""
    apply(
        store,
        cs(
            area_concept("a", "one"),
            area_concept("b", "one"),
            area_concept("c", "two"),
            area_concept("d", "two"),
            area_concept("x", "two"),
            area_concept("z", "two"),
            prereq("a", "b"),
            prereq("b", "c"),
            prereq("c", "d"),
            related("d", "x"),
        ),
    )
    return graph(db)


def test_connect_adds_the_shortest_bridge(chain):
    members, bridges, ok = connect(chain, ["a", "d"], max_hops=3)
    assert members == ["a", "d"] and bridges == ["b", "c"] and ok


def test_connect_respects_hop_limit_and_reports_disconnection(chain):
    _, bridges, ok = connect(chain, ["a", "d"], max_hops=1)
    assert bridges == [] and not ok
    _, _, ok = connect(chain, ["a", "z"], max_hops=5)  # z has no relations at all
    assert not ok


def test_connect_uses_weak_edges_only_as_a_last_resort(chain):
    _, bridges, ok = connect(chain, ["c", "x"], max_hops=3)
    assert bridges == ["d"] and ok  # only reachable through the "related" edge


def test_needed_for_is_the_closure_in_learning_order(chain):
    assert needed_for(chain, "c") == ["a", "b", "c"]


def inputs(g, **kw):
    return ViewInputs(
        graph=g,
        settings=kw.pop("settings", ViewSettings()),
        areas=[Area(id="one", title="One"), Area(id="two", title="Two")],
        **kw,
    )


def test_providers_build_overlapping_views(chain):
    test = GraphTest(question="Why does d need a?")
    trace = b.TestTrace(test_id=test.id, steps=[TraceStep(text="s", concept_ids=["a", "d"])])
    views = compute_views(inputs(chain, tests=[test], traces={test.id: trace}))
    by_id = {v.id: v for v in views}
    assert by_id["area:one"].concept_ids == ["a", "b"]  # the partition, as assigned
    q = by_id[f"question:{test.id}"]
    assert q.kind is ViewKind.QUESTION and q.bridge_ids == ["b", "c"] and q.connected
    assert set(q.all_ids) & set(by_id["area:one"].concept_ids)  # views may overlap
    assert any(v.kind is ViewKind.OVERVIEW for v in views)


def test_saved_views_and_custom_providers(chain):
    me = Actor(kind="human", name="expert")
    saved = SavedView(title="Mine", kind=ViewKind.SUBTOPIC, concept_ids=["a", "x"], author=me)

    @register_view_provider("test-everything")
    def everything(inp):
        return [needed_for_view(inp, "d")]

    try:
        settings = ViewSettings(providers=["saved", "test-everything"])
        views = compute_views(inputs(chain, saved=[saved], settings=settings))
        assert views[0].saved and views[0].concept_ids == ["a", "x"]  # kept exactly as chosen
        assert views[0].bridge_ids == []
        assert views[1].concept_ids == ["a", "b", "c", "d"] and views[1].focus_ids == ["d"]
    finally:
        VIEW_PROVIDERS.pop("test-everything")
    with pytest.raises(HocError):
        compute_views(inputs(chain, settings=ViewSettings(providers=["nope"])))


def test_view_service_over_a_built_topic(workspace):
    run_until_idle(workspace)  # recorded GCD/LCM build: areas, tests and traces
    views = ViewService(workspace.app)
    seq = workspace.app.changesets.last_seq()
    listed = views.all()
    kinds = {v["kind"] for v in listed}
    assert {"overview", "area", "question"} <= kinds
    assert all(v["connected"] for v in listed if v["kind"] == "question")

    cid = listed[0]["concept_ids"][0]
    assert views.needed_for(cid)["focus_ids"] == [cid]
    assert cid in views.around(cid)["concept_ids"]

    saved = views.save("My subtopic", [cid, "not-a-concept"], kind="subtopic")
    mine = next(v for v in views.all() if v["id"] == saved["id"])
    assert mine["saved"] and mine["concept_ids"] == [cid] and mine["kind"] == "subtopic"
    with pytest.raises(HocError):
        views.save("Empty", ["not-a-concept"])
    views.delete(saved["id"])
    assert all(v["id"] != saved["id"] for v in views.all())
    assert workspace.app.changesets.last_seq() == seq  # views never change the graph
