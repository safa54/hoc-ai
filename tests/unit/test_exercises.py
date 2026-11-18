"""Exercise bank: extraction, matching, routing, retrieval and grading (L-M5 checks)."""

from typing import Any

import pytest

from hoc.assess import Grader, TaskBank, check_exact, parse_number
from hoc.config import load_settings
from hoc.construct.agents.materials import ExtractOutput, MatchOutput  # noqa: F401
from hoc.core.assessment import Approach, Requirement, Rubric, Task, TaskType
from hoc.core.build import Area, Blueprint, Brief, FeedbackStatus, Progress, RunMode
from hoc.core.changes import MergeConcepts
from hoc.core.errors import HocError
from hoc.core.refs import Facet, TargetKind, TargetRef
from hoc.llm.testing import ScriptedBackend
from hoc.services import HocApp, LearningSpace, TopicWorkspace
from hoc.store import Database
from tests.conftest import apply, cs
from tests.unit.test_tutor import build_graph


def req(cid: str, facet: Facet = Facet.APPLY, weight: str = "primary") -> Requirement:
    return Requirement(
        ref=TargetRef(kind=TargetKind.CONCEPT, id=cid, facet=facet),
        weight=weight,  # type: ignore[arg-type]
    )


def task(**over: Any) -> Task:
    base: dict[str, Any] = {
        "title": "gcd of 12 and 18",
        "prompt": "What is gcd(12, 18)?",
        "task_type": TaskType.NUMERIC,
        "rubric": Rubric(correct_answers=["6"]),
        "approaches": [
            Approach(id="a1", requirements=[req("gcd"), req("divisibility", weight="secondary")])
        ],
        "match_status": "confirmed",
        "match_confidence": 0.9,
        "solution_text": "Factors: 6.",
    }
    base.update(over)
    return Task(**base)


class Rig:
    def __init__(self, db: Database, responder=None):
        self.backend = ScriptedBackend(responder=responder or (lambda r: {}))
        self.app = HocApp(load_settings(), db, self.backend)
        build_graph(self.app)
        self.space = LearningSpace(self.app)
        self.session = self.space.session("me")["session"]["id"]


# --------------------------------------------------------------- exact graders


def test_numbers_parse_like_a_teacher_would():
    assert parse_number("6") == 6 and parse_number(" 1,5 ") == parse_number("3/2")
    assert parse_number("12 cm") == 12 and parse_number("\u22122") == -2
    assert parse_number("six") is None


def test_exact_checks():
    t = task()
    assert check_exact(t, "6") is True and check_exact(t, "6.0") is True
    assert check_exact(t, "7") is False and check_exact(t, "no idea") is None
    mcq = task(
        task_type=TaskType.MCQ, options=["4", "6", "12"], rubric=Rubric(correct_answers=["6"])
    )
    assert check_exact(mcq, "6") is True and check_exact(mcq, "b") is True
    assert check_exact(mcq, "a") is False


def test_exact_grading_makes_evidence_for_the_approach_requirements(db):
    rig = Rig(db)
    grader = Grader(rig.app.settings.learn, None, rig.app.graph())
    right = grader.grade("me", task(), "6")
    assert right.correct and right.graded_by == "exact"
    by_concept = {o.target.id: o for o in right.observations}
    assert by_concept["gcd"].weight == pytest.approx(1.0)
    assert by_concept["divisibility"].weight == pytest.approx(0.5)  # secondary requirement
    assert {o.judge.kind.value for o in right.observations} == {"exact"}
    wrong = grader.grade("me", task(), "7", hints_used=1)
    assert not wrong.correct and {o.outcome.value for o in wrong.observations} == {"absent"}
    assert wrong.observations[0].weight == pytest.approx(0.5)  # hint discount 1/(1+1)
    lucky = grader.grade("me", task(task_type=TaskType.MCQ, options=["4", "6", "12"]), "6")
    assert lucky.observations[0].weight == pytest.approx(1 - 1 / 3, abs=1e-3)  # guess correction
    unread = grader.grade("me", task(), "no idea")
    assert unread.correct is None and unread.observations == []
    assert rig.backend.requests == []  # none of this needed a model


# --------------------------------------------------------------------- learn


def open_answer_task() -> Task:
    return task(
        task_type=TaskType.SHORT_ANSWER,
        rubric=Rubric(expected_key_ideas=["largest shared"]),
        prompt="Explain how to find gcd(12, 18).",
    )


def grade_output(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "correct": True,
        "approach_id": "a1",
        "feedback": "Good.",
        "outcomes": [
            {
                "concept_id": "gcd",
                "facet": "apply",
                "outcome": "strong",
                "quote": "common factors",
                "rationale": "lists them",
            },
            {
                "concept_id": "divisibility",
                "facet": "apply",
                "outcome": "absent",
                "quote": "",
                "rationale": "not shown",
            },
        ],
    }
    base.update(over)
    return base


def test_learner_never_sees_the_answer_key(db):
    rig = Rig(db)
    rig.app.task_store.save(task())
    listed = rig.space.tasks("me", "gcd")
    opened = rig.space.open_task("me", listed[0]["id"])
    for payload in (listed[0], opened["task"]):
        assert "rubric" not in payload and "solution_text" not in payload
        assert "Factors: 6" not in str(payload) and "correct_answers" not in str(payload)
    assert opened["concepts"][0]["id"] == "gcd"


def test_open_answers_cost_exactly_one_call_and_exact_answers_none(db):
    rig = Rig(db, responder=lambda r: grade_output())
    rig.app.task_store.save(task(id="t-exact"))
    rig.app.task_store.save(open_answer_task().model_copy(update={"id": "t-open"}))
    a1 = rig.space.open_task("me", "t-exact")["attempt"]["id"]
    r1 = rig.space.answer_task("me", a1, "6", rig.session)
    assert r1["correct"] and r1["graded_by"] == "exact" and rig.backend.requests == []
    a2 = rig.space.open_task("me", "t-open")["attempt"]["id"]
    r2 = rig.space.answer_task(
        "me", a2, "I list the common factors and pick the largest", rig.session
    )
    assert len(rig.backend.requests) == 1 and r2["graded_by"] == "llm"
    assert [o["target"]["id"] for o in r2["recorded"]] == ["gcd", "divisibility"]
    assert rig.app.observations.count("me") == 4  # 2 exact + 2 graded
    assert rig.space.engine.budget.usage(rig.session)["session_calls"] == 1  # grading is budgeted


def test_llm_grader_drops_unsupported_evidence(db):
    bad = grade_output(
        outcomes=[
            {
                "concept_id": "gcd",
                "facet": "apply",
                "outcome": "strong",
                "quote": "never written",
                "rationale": "x",
            },  # quote not in answer
            {
                "concept_id": "euclid",
                "facet": "apply",
                "outcome": "strong",
                "quote": "common factors",
                "rationale": "x",
            },  # not in the approach
            {
                "concept_id": "divisibility",
                "facet": "apply",
                "outcome": "partial",
                "quote": "common factors",
                "rationale": "slip",
            },  # valid
        ],
        approach_id="does-not-exist",
    )
    rig = Rig(db, responder=lambda r: bad)
    rig.app.task_store.save(open_answer_task().model_copy(update={"id": "t1"}))
    attempt = rig.space.open_task("me", "t1")["attempt"]["id"]
    out = rig.space.answer_task("me", attempt, "I take the common factors", rig.session)
    assert [(o["target"]["id"], o["outcome"]) for o in out["recorded"]] == [
        ("divisibility", "partial")
    ]
    assert out["attempt"]["approach_id"] == "a1"  # unknown approach falls back to the first


def test_revealing_the_solution_is_weak_evidence_and_ends_evidence(db):
    rig = Rig(db)
    rig.app.task_store.save(task(id="t1"))
    attempt = rig.space.open_task("me", "t1")["attempt"]["id"]
    shown = rig.space.reveal_task("me", attempt)
    assert shown["solution"] == "Factors: 6."
    obs = rig.app.observations.for_learner("me")
    assert obs and {o.outcome.value for o in obs} == {"absent"} and obs[0].weight < 0.3
    later = rig.space.answer_task("me", attempt, "6", rig.session)
    assert later["correct"] and later["recorded"] == [] and later["evidence_skipped"]
    assert rig.app.observations.count("me") == len(obs)


def test_hints_discount_later_evidence_and_use_one_tutor_call(db):
    reply = {
        "reply": "Think about factors.",
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
    rig = Rig(db, responder=lambda r: reply)
    rig.app.task_store.save(task(id="t1"))
    attempt = rig.space.open_task("me", "t1")["attempt"]["id"]
    hint = rig.space.hint_task("me", attempt, rig.session)
    assert hint["hints_used"] == 1 and len(rig.backend.requests) == 1
    context = rig.backend.requests[0].user
    assert "What is gcd(12, 18)?" in context and "Factors: 6" not in context  # no solution leak
    done = rig.space.answer_task("me", attempt, "6", rig.session)
    assert done["recorded"][0]["hints_used"] == 1 and done["recorded"][0]["weight"] == 0.5


def test_unconfirmed_tasks_are_not_offered(db):
    rig = Rig(db)
    rig.app.task_store.save(task(id="t1", match_status="proposed"))
    assert rig.space.tasks("me", "gcd") == []
    with pytest.raises(HocError, match="not available"):
        rig.space.open_task("me", "t1")


def test_task_links_follow_merges(db):
    rig = Rig(db)
    rig.app.task_store.save(task(id="t1"))
    bank = TaskBank(rig.app.task_store, rig.app.graph)
    assert [t.id for t in bank.for_concept("gcd")] == ["t1"]
    apply(rig.app.changesets, cs(MergeConcepts(from_id="gcd", into_id="lcm")))
    assert [t.id for t in TaskBank(rig.app.task_store, rig.app.graph).for_concept("lcm")] == ["t1"]


def test_policy_offers_practice_for_a_concept_with_exercises(db):
    rig = Rig(db)
    assert all(m["kind"] != "practice" for m in rig.space.next_moves("me", "gcd")["moves"])
    rig.app.task_store.save(task(id="t1"))
    kinds = [m["kind"] for m in rig.space.next_moves("me", "gcd")["moves"]]
    assert "practice" in kinds


# ------------------------------------------------------- build side: agents


def extract_match_responder(request) -> dict[str, Any]:
    if request.schema_name == "ExtractOutput":
        return {
            "tasks": [
                {
                    "title": "GCD of 12 and 18",
                    "prompt": "Find the greatest common divisor of 12 and 18.",
                    "solution": "6",
                    "task_type": "numeric",
                    "options": [],
                    "correct_answers": ["6"],
                },
                {
                    "title": "Why Euclid works",
                    "prompt": "Explain why the Euclidean algorithm works.",
                    "solution": "",
                    "task_type": "explain_why",
                    "options": [],
                    "correct_answers": [],
                },
                {
                    "title": "dup",
                    "prompt": "Find the greatest common divisor of 12 and 18.",
                    "solution": "",
                    "task_type": "numeric",
                    "options": [],
                    "correct_answers": [],
                },
            ]
        }
    return {
        "items": [
            {
                "index": 0,
                "confidence": 0.95,
                "unmapped": [],
                "approaches": [
                    {
                        "label": "factors",
                        "requirements": [
                            {"concept_id": "gcd", "facet": "apply", "weight": "secondary"},
                            {"concept_id": "gcd", "facet": "apply", "weight": "primary"},  # twice
                            {
                                "concept_id": "not-in-graph",
                                "facet": "apply",
                                "weight": "secondary",
                            },  # not shortlisted
                        ],
                    }
                ],
            },
            {
                "index": 1,
                "confidence": 0.4,
                "unmapped": ["proof by contradiction"],
                "approaches": [
                    {
                        "label": "argument",
                        "requirements": [
                            {"concept_id": "euclid", "facet": "explain", "weight": "primary"}
                        ],
                    }
                ],
            },
        ]
    }


@pytest.fixture
def lab(tmp_path):
    ws = TopicWorkspace(
        HocApp.open(
            db_path=tmp_path / "lab.db", backend=ScriptedBackend(responder=extract_match_responder)
        )
    )
    build_graph(ws.app)
    ws.set_brief(Brief(title="GCD"))
    ws.app.meta.put("blueprint", Blueprint(areas=[Area(id="area-1", title="Area 1")]))
    ws.app.meta.put("progress", Progress(built_areas=["area-1"], completed=True))
    yield ws
    ws.close()


def test_extract_then_match_with_routing(lab):
    lab.add_material(
        "Sheet 1", text="1. Find the greatest common divisor of 12 and 18.\n\n2. Explain."
    )
    assert lab.engine.next_task().role == "extractor"
    lab.engine.step(RunMode.AUTO)
    tasks = lab.tasks()
    assert len(tasks) == 2 and {t["match_status"] for t in tasks} == {"unmatched"}  # dup dropped
    assert lab.engine.next_task().role == "matcher"
    lab.engine.step(RunMode.AUTO)
    by_title = {t["title"]: t for t in lab.tasks()}
    sure, unsure = by_title["GCD of 12 and 18"], by_title["Why Euclid works"]
    assert sure["match_status"] == "confirmed"  # confident + auto mode: goes live
    assert [x["weight"] for x in sure["links"]] == ["primary"]  # duplicate merged, primary wins
    assert [x["concept_id"] for x in sure["links"]] == ["gcd"]  # decoy dropped, duplicate merged
    assert sure["links"][0]["weight"] == "primary"
    assert unsure["match_status"] == "proposed" and unsure["unmapped"] == ["proof by contradiction"]
    notes = lab.app.feedback.find(FeedbackStatus.OPEN)
    assert any("proof by contradiction" in n.text for n in notes)  # gap -> feedback for the refiner
    assert lab.engine.next_task() is None or lab.engine.next_task().role not in (
        "extractor",
        "matcher",
    )
    # the exercise sheet is not teaching material for planner/builders
    assert lab.engine._context(RunMode.AUTO).chunks == []


def test_manual_mode_never_confirms_by_itself(lab):
    lab.add_task(
        "Find the greatest common divisor of 20 and 30.",
        task_type="numeric",
        correct_answers=["10"],
    )
    lab.engine.step(RunMode.MANUAL)  # matcher
    only = lab.tasks()[0]
    assert only["match_status"] == "proposed"


def test_expert_confirms_edits_and_rejects_matches(lab):
    lab.add_task(
        "Find the greatest common divisor of 20 and 30.",
        task_type="numeric",
        correct_answers=["10"],
    )
    task_id = lab.tasks()[0]["id"]
    with pytest.raises(HocError, match="link at least one"):
        lab.task_decision(task_id, "confirm")
    edited = lab.task_decision(
        task_id,
        "edit",
        [
            {
                "label": "factors",
                "requirements": [
                    {"concept_id": "gcd", "facet": "apply", "weight": "primary"},
                    {"concept_id": "ghost", "facet": "apply", "weight": "primary"},
                ],
            }
        ],
    )
    assert edited["match_status"] == "confirmed" and [x["concept_id"] for x in edited["links"]] == [
        "gcd"
    ]
    assert LearningSpace(lab.app).tasks("me", "gcd")  # now available to learners
    assert lab.task_decision(task_id, "reject")["match_status"] == "rejected"
    assert LearningSpace(lab.app).tasks("me", "gcd") == []
    assert lab.task_decision(task_id, "rematch")["match_status"] == "unmatched"
