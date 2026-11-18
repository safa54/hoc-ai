"""The tutor turn: cost-bounded, validated, logged (LEARNING_SIDE_PLAN L5, L-M3 checks)."""

from typing import Any

import pytest

from hoc.config import load_settings, with_overrides
from hoc.core.changes import AddConcept, AddRelation
from hoc.core.errors import LLMError
from hoc.core.knowledge import (
    Concept,
    ConceptCard,
    KeyIdea,
    Necessity,
    Relation,
    RelationType,
)
from hoc.llm.testing import ScriptedBackend
from hoc.services import HocApp, LearningSpace
from hoc.store import Database
from tests.conftest import apply, cs

LONG = "Divisibility " * 400
SECRET = "ZZUNRELATEDZZ"


def card(text: str, long: bool = False, secret: bool = False) -> ConceptCard:
    return ConceptCard(
        one_line=text,
        definition=LONG if long else (f"{SECRET} body" if secret else f"Definition of {text}"),
        key_ideas=[KeyIdea(id="KI1", text=f"key idea of {text}")],
        examples=["ex 1", "ex 2", "ex 3"],
    )


def build_graph(app: HocApp) -> None:
    def c(id_: str, name: str, long: bool = False, secret: bool = False) -> AddConcept:
        return AddConcept(
            concept=Concept(id=id_, name=name, card=card(name, long, secret), topic_ids=["area-1"])
        )

    def rel(t: RelationType, a: str, b: str, nec: Necessity | None = None) -> AddRelation:
        return AddRelation(relation=Relation(type=t, source=a, target=b, necessity=nec))

    ops = [
        c("divisibility", "Divisibility"),
        c("gcd", "Greatest common divisor", long=True),
        c("lcm", "Least common multiple"),
        c("euclid", "Euclidean algorithm"),
        c("far-away", "Far away concept", secret=True),
        rel(RelationType.PREREQUISITE_OF, "divisibility", "gcd", Necessity.ESSENTIAL),
        rel(RelationType.PREREQUISITE_OF, "gcd", "euclid", Necessity.ESSENTIAL),
        rel(RelationType.ANALOGOUS_TO, "gcd", "lcm"),
    ]
    apply(app.changesets, cs(*ops))


def ok_output(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "reply": "A **GCD** is the largest shared divisor.",
        "move": -1,
        "evidence": [],
        "check_question": "",
        "check_concept_id": "",
        "check_facet": "apply",
        "check_expected": [],
        "check_result": "",
        "note": "",
        "graph_gap": "",
    }
    base.update(over)
    return base


class Harness:
    def __init__(self, db, learn_overrides=None, output=None):
        self.outputs = [output or ok_output()]
        self.backend = ScriptedBackend(responder=self._respond)
        settings = load_settings()
        if learn_overrides:
            settings = with_overrides(settings, {"learn": learn_overrides})
        self.app = HocApp(settings, db, self.backend)
        build_graph(self.app)
        self.space = LearningSpace(self.app)
        self.session = self.space.session("me")["session"]["id"]

    def _respond(self, request):
        out = self.outputs[0] if len(self.outputs) == 1 else self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def say(self, text="hello", intent="message", focus="gcd"):
        return self.space.turn("me", self.session, text, intent, focus)

    @property
    def calls(self) -> int:
        return len(self.backend.requests)


@pytest.fixture
def h(db):
    return Harness(db)


def test_exactly_one_call_per_message_and_none_for_closed_actions(h):
    h.say("what is a gcd? I think it is the biggest number dividing both")
    assert h.calls == 1
    h.say("and an lcm?", focus="lcm")
    assert h.calls == 2
    for intent in ("next", "got_it", "lost", "exercise"):
        h.say("", intent)
    assert h.calls == 2  # closed actions made no LLM call at all
    reply = h.say("", "quiz")  # quick actions that need the tutor cost exactly one
    assert h.calls == 3 and reply["cost"]["calls"] == 1
    assert h.space.session_cost(h.session)["calls"] == 3


def test_closed_actions_still_record_and_explain(h):
    r = h.say("", "lost", "gcd")
    assert r["observations"] and r["observations"][0]["judge"]["kind"] == "self_report"
    assert any(s["kind"] == "review_prerequisite" for s in r["suggestions"]) or r["suggestions"]
    state = h.space.state("me")
    assert state.concepts["gcd"].self_flag.value == "lost"
    nxt = h.say("", "next", None)
    assert nxt["turn"]["role"] == "tutor" and nxt["decision_id"]
    assert h.space.decisions("me")[-1]["focus_concept_id"] is None


def test_context_respects_budgets_and_leaves_out_non_neighbours(h):
    h.say("explain", "explain", "gcd")
    meta = h.app.turns.get(h.space.turns(h.session)[-1]["id"]).meta
    budgets = h.app.settings.learn.context
    for section, size in meta["sizes"].items():
        assert size <= getattr(budgets, section if section != "map" else "map"), section
    assert meta["sizes"]["focus"] <= budgets.focus < len(LONG)  # the huge card was clipped
    ctx = meta["context"]
    assert "Euclidean algorithm" in ctx and "Divisibility" in ctx and "Least common" in ctx
    assert SECRET not in ctx  # a non-neighbour's card never reaches the tutor
    assert "Far away concept" in ctx  # only its name, as a suggested move target
    assert sum(meta["sizes"].values()) < 9000


def test_overview_scope_sends_names_only(h):
    h.say("how does this fit together?", "overview", None)
    meta = h.app.turns.get(h.space.turns(h.session)[-1]["id"]).meta
    assert "map" in meta["sizes"] and "focus" not in meta["sizes"]
    assert "Far away concept" in meta["context"]  # names appear...
    assert (
        SECRET not in meta["context"] and "Definition of" not in meta["context"]
    )  # ...cards do not


def test_invented_evidence_is_dropped(db):
    out = ok_output(
        evidence=[
            {
                "concept_id": "gcd",
                "facet": "apply",
                "outcome": "strong",
                "quote": "I divide both by 6",
                "rationale": "ok",
            },  # never said
            {
                "concept_id": "ghost",
                "facet": "apply",
                "outcome": "strong",
                "quote": "biggest number",
                "rationale": "x",
            },  # unknown concept
            {
                "concept_id": "far-away",
                "facet": "apply",
                "outcome": "strong",
                "quote": "biggest number",
                "rationale": "x",
            },  # not in the context
            {
                "concept_id": "gcd",
                "facet": "recall",
                "outcome": "strong",
                "quote": "BIGGEST   number",
                "rationale": "states the idea",
            },  # valid (normalised)
        ]
    )
    h = Harness(db, output=out)
    r = h.say("I think the gcd is the biggest number dividing both")
    assert [(o["target"]["id"], o["target"]["facet"]) for o in r["observations"]] == [
        ("gcd", "recall")
    ]
    stored = h.app.observations.for_learner("me")
    assert len(stored) == 1 and stored[0].judge.kind.value == "llm"
    assert stored[0].weight == pytest.approx(0.7)


def test_check_question_is_judged_in_the_next_call(db):
    ask = ok_output(
        check_question="What is gcd(12, 18)?",
        check_concept_id="gcd",
        check_facet="apply",
        check_expected=["6"],
    )
    verdict = ok_output(check_result="contradicting", reply="Not quite.")
    h = Harness(db)
    h.outputs = [ask, verdict]
    r1 = h.say("explain", "explain")
    assert r1["check"]["question"].startswith("What is gcd")
    assert h.app.notes.pending_check("me") is not None
    r2 = h.say("it is 36")
    assert h.calls == 2  # grading the answer needed no extra call
    assert [(o["target"]["id"], o["outcome"]) for o in r2["observations"]] == [
        ("gcd", "contradicting")
    ]
    assert h.app.notes.pending_check("me") is None
    assert "it is 36" in r2["observations"][0]["quote"]


def test_move_choice_is_validated_against_the_offered_moves(db):
    h = Harness(db, output=ok_output(move=99))
    assert h.say("explain", "explain")["suggestions"] == []  # out of range: ignored
    other = Database(":memory:")
    other.migrate()
    h2 = Harness(other, output=ok_output(move=0))
    reply = h2.say("explain", "explain", "gcd")
    assert len(reply["suggestions"]) == 1  # an in-range index maps to the policy's own move
    decision = h2.space.decisions("me")[-1]
    assert decision["chosen_by"] == "tutor" and decision["chosen_index"] == 0


def test_budget_guard_stops_calls_without_failing(db):
    h = Harness(db, learn_overrides={"budget": {"max_llm_calls_per_session": 2}})
    h.say("one")
    h.say("two")
    blocked = h.say("three")
    assert h.calls == 2 and "budget" in blocked["turn"]["text"].lower()
    assert blocked["cost"]["calls"] == 0
    assert h.say("", "next")["turn"]["role"] == "tutor"  # closed actions still work


def test_model_failure_is_a_graceful_turn(db):
    h = Harness(db)
    h.outputs = [LLMError("boom")]
    r = h.say("hello there")
    assert "could not answer" in r["turn"]["text"] and r["observations"] == []
    assert r["cost"]["calls"] == 0
    assert h.app.observations.count("me") == 0


def test_graph_gaps_become_feedback_for_the_studio(db):
    h = Harness(db, output=ok_output(graph_gap="Prime factorisation is needed here"))
    h.say("why does the euclidean algorithm work?", focus="euclid")
    items = h.app.feedback.find()
    assert len(items) == 1 and items[0].author.name == "tutor"
    assert items[0].target_id == "euclid" and "Prime factorisation" in items[0].text
    assert h.app.graph().concepts()  # and the graph itself was not touched
    assert h.app.changesets.last_seq() == 1


def test_notes_and_decisions_are_logged_and_debuggable(db):
    h = Harness(db, output=ok_output(note="Prefers examples", move=0))
    r = h.say("explain", "explain", "gcd")
    assert h.app.notes.get("me").entries[-1].text == "Prefers examples"
    dbg = h.space.turn_debug(r["turn"]["id"])
    assert dbg["meta"]["context"] and dbg["meta"]["raw"]["note"] == "Prefers examples"
    assert dbg["decision"]["candidates"] and dbg["decision"]["chosen_by"] in ("tutor", "policy")
    assert dbg["budget"]["session_limit"] == 120


def test_highlights_are_limited_to_concepts_the_tutor_was_shown(db):
    h = Harness(db, output=ok_output(highlight=["divisibility", "gcd", "far-away", "nope", "gcd"]))
    assert h.say("how do these connect?")["highlight"] == ["divisibility", "gcd"]


def test_a_goal_is_an_interaction_that_shapes_the_path_not_the_beliefs(db):
    h = Harness(db)
    whole = h.space.path("me")
    assert whole["goal"] is None and set(whole["ids"]) >= {"divisibility", "gcd", "euclid", "lcm"}
    h.space.interact("me", "goal", "euclid")
    path = h.space.graph("me")["path"]
    assert path["ids"] == ["divisibility", "gcd", "euclid"] and path["next"] == "divisibility"
    assert path["goal_name"] == "Euclidean algorithm" and path["done"] == []
    assert h.space.next_moves("me", None)["moves"][0]["reason"] == "Next step towards your goal"
    assert h.space.observations("me") == []  # choosing a goal is not evidence
    h.space.interact("me", "goal", None)
    assert h.space.path("me")["goal"] is None
