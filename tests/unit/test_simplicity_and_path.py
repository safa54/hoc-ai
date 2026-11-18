"""Prompt size, simplicity rules and the learning path (cost / navigation changes)."""

from hoc.config import AgentSettings, LearnSettings
from hoc.construct.context import mentioned_concepts, render_graph
from hoc.construct.coordinator import CoordinatorState, PhasedCoordinator
from hoc.construct.validators import redundant_prerequisite_edges
from hoc.core.build import (
    Area,
    Blueprint,
    Brief,
    Feedback,
    FeedbackKind,
    GraphTest,
    Progress,
    RunMode,
)
from hoc.core.changes import AddConcept
from hoc.core.common import Actor
from hoc.core.knowledge import Concept, ConceptCard, KeyIdea, Necessity
from hoc.core.learning import LearnerState
from hoc.policy import PolicyContext, create_policy, learning_path
from tests.conftest import AGENT, apply, cs, graph, prereq
from tests.unit.test_construct import run_until_idle


def node(id_: str, area: str = "", text: str = "x") -> AddConcept:
    card = ConceptCard(
        one_line=f"{id_} summary " + text * 60,
        definition=f"{id_} definition " + text * 300,
        key_ideas=[KeyIdea(id="KI1", text=f"{id_} idea")],
    )
    return AddConcept(
        concept=Concept(id=id_, name=id_, card=card, topic_ids=[area] if area else [])
    )


def note(text: str, severity: str = "medium", **kw) -> Feedback:
    return Feedback(kind=FeedbackKind.ISSUE, text=text, severity=severity, author=AGENT, **kw)


# ---------------------------------------------------------------------------- cost


def test_graph_text_shows_full_cards_only_where_asked(db, store):
    apply(store, cs(node("a"), node("b"), node("c"), prereq("a", "b"), prereq("a", "c")))
    g = graph(db)
    outline = render_graph(g, summary_chars=40, definition_chars=100)
    scoped = render_graph(g, detail={"b"}, summary_chars=40, definition_chars=100)
    full = render_graph(g, detail=True, summary_chars=40, definition_chars=100)
    assert "definition" not in outline and "KI1" not in outline
    assert scoped.count("definition:") == 1 and "b idea" in scoped and "a idea" not in scoped
    assert len(outline) < len(scoped) < len(full)
    assert "- prerequisite_of: a -> b, c" in outline  # relations grouped by source
    assert max(len(line) for line in outline.splitlines()) < 120  # summaries are clipped


def test_notes_name_the_concepts_the_refiner_needs_in_full(db, store):
    apply(store, cs(node("a"), node("b"), node("c"), prereq("a", "b")))
    g = graph(db)
    items = [
        note("too vague", target_kind="concept", target_id="c"),
        note("wrong way round", target_kind="relation", target_id="prerequisite_of:a->b"),
    ]
    assert mentioned_concepts(g, items) == {"a", "b", "c"}
    assert mentioned_concepts(g, [note("missing relation a -> c", target_kind="graph")]) == {
        "a",
        "c",
    }


def test_only_chains_at_least_as_strong_make_an_edge_redundant(db, store):
    sup = Necessity.SUPPORTIVE
    apply(
        store,
        cs(
            *(node(x) for x in "abcd"),
            prereq("a", "b"),
            prereq("b", "c"),
            prereq("a", "c"),  # implied by the essential chain a -> b -> c
            prereq("c", "d", sup),
            prereq("b", "d"),  # essential, but the chain b -> c -> d is only supportive
        ),
    )
    assert [r.id for r in redundant_prerequisite_edges(graph(db))] == ["prerequisite_of:a->c"]


def test_critic_reads_only_what_changed_and_nothing_costs_nothing(workspace):
    from dataclasses import replace

    from hoc.construct.agents import create_agent
    from hoc.core.build import AgentTask

    run_until_idle(workspace)
    db = workspace.engine.s.db

    def calls() -> list[str]:
        with db.read() as conn:
            rows = conn.execute("SELECT data FROM llm_calls WHERE step = 'agent_critic'")
            return [r["data"] for r in rows]

    ctx = workspace.engine._context(RunMode.AUTO)
    task = AgentTask(role="critic", label="again")
    n = len(calls())
    seen = {c.id: c.version for c in ctx.graph.concepts()}
    unchanged = replace(ctx, reviewed_seq=ctx.graph_seq, reviewed_versions=seen)
    create_agent("critic").run(task, unchanged)
    assert len(calls()) == n  # same graph as last review: no model call

    one = ctx.graph.concepts()[0].id
    partly = replace(ctx, reviewed_seq=-1, reviewed_versions={**seen, one: 0})
    create_agent("critic").run(task, partly)
    prompt = calls()[-1]
    assert len(calls()) == n + 1 and prompt.count("definition: ") == 1  # one card in full


def test_coordinator_parks_minor_notes_and_skips_covered_tests():
    settings = AgentSettings(min_tests=1)
    areas = [Area(id="x", title="X")]
    test = GraphTest(question="q", expected="e", origin="agent")
    base = {
        "brief": Brief(title="T"),
        "blueprint": Blueprint(summary="", grain_guide="", areas=areas),
        "tests": [test],
        "settings": settings,
        "queue": [],
    }
    done = Progress(built_areas=["x"], critic_done=True, tests_generated=True, traced=[test.id])
    low = CoordinatorState(progress=done, feedback=[note("nit", "low")], **base)
    assert PhasedCoordinator().next_task(low) is None  # a nit never costs a refiner call
    medium = CoordinatorState(progress=done, feedback=[note("real", "medium")], **base)
    assert PhasedCoordinator().next_task(medium).role == "refiner"
    expert = Feedback(
        kind=FeedbackKind.COMMENT, text="nit", severity="low", author=Actor(kind="human", name="e")
    )
    assert (
        PhasedCoordinator()
        .next_task(CoordinatorState(progress=done, feedback=[expert], **base))
        .role
        == "refiner"
    )  # the expert is always heard

    round2 = done.model_copy(update={"round": 1, "traced": []})
    again = CoordinatorState(progress=round2, feedback=[], **base)
    assert PhasedCoordinator().next_task(again).test_id == test.id
    covered = CoordinatorState(progress=round2, feedback=[], covered_tests={test.id}, **base)
    assert PhasedCoordinator().next_task(covered) is None


# ---------------------------------------------------------------------- navigation


def two_areas(store) -> None:
    """Area p: p1 -> p2 -> p3.  Area q: q1 -> q2, and q2 also needs p1."""
    apply(
        store,
        cs(
            node("p1", "p"),
            node("p2", "p"),
            node("p3", "p"),
            node("q1", "q"),
            node("q2", "q"),
            prereq("p1", "p2"),
            prereq("p2", "p3"),
            prereq("q1", "q2"),
            prereq("p1", "q2"),
        ),
    )


def test_path_finishes_an_area_before_opening_the_next(db, store):
    two_areas(store)
    g = graph(db)
    assert learning_path(g, area_order=["p", "q"]) == ["p1", "p2", "p3", "q1", "q2"]
    assert learning_path(g, area_order=["q", "p"]) == ["q1", "p1", "p2", "p3", "q2"]


def test_goal_path_is_only_what_the_goal_needs(db, store):
    two_areas(store)
    g = graph(db)
    assert learning_path(g, goal="q2", area_order=["p", "q"]) == ["p1", "q1", "q2"]
    assert learning_path(g, goal="nope") == learning_path(g)  # unknown goal: whole topic


def test_policy_puts_the_next_path_step_first(db, store):
    two_areas(store)
    g = graph(db)

    def top(**kw) -> list[str]:
        ctx = PolicyContext(
            state=LearnerState(learner_id="me", model_id="m"),
            graph=g,
            settings=LearnSettings(),
            **kw,
        )
        moves = create_policy("heuristic").candidates(ctx)
        return [m.concept_id for m in moves if m.kind == "advance"]

    assert top(area_order=["p", "q"])[0] == "p1"
    assert top(area_order=["q", "p"])[0] == "q1"
    assert top(area_order=["p", "q"], goal="q2")[:2] == ["p1", "q1"]
