"""Recorded-fixture replay of the learning side, using real model outputs captured from a live
GCD/LCM session (see data/examples/gcd_lcm_fixture.json). No network."""

import pytest

from hoc.core.build import RunMode
from hoc.core.learning import BeliefStatus
from hoc.services import LearningSpace
from tests.unit.test_construct import run_until_idle

WRONG = "It is 60, I took the smallest number that both 12 and 20 go into."
ANSWER = (
    "gcd takes the smaller exponent of each prime and lcm takes the larger exponent, so for every "
    "prime the two exponents together add up to the exponents in a times b, hence gcd times lcm "
    "equals a times b."
)


@pytest.fixture
def rig(workspace):
    run_until_idle(workspace)  # builds the 66-concept graph from recorded builder outputs
    workspace.add_material("Problem set 1", text="1. exercises\n\n2. more exercises")
    workspace.engine.step(RunMode.AUTO)  # extractor (recorded real output)
    workspace.engine.step(RunMode.AUTO)  # matcher (recorded real output)
    space = LearningSpace(workspace.app)
    return workspace, space, space.session("me")["session"]["id"]


def test_extractor_and_matcher_on_real_output(rig):
    ws, _, _ = rig
    tasks = ws.tasks()
    assert len(tasks) == 6
    assert {t["task_type"] for t in tasks} >= {"numeric", "mcq", "explain_why", "worked_problem"}
    mcq = next(t for t in tasks if t["task_type"] == "mcq")
    assert mcq["options"] == ["2", "10", "12", "24"] and mcq["rubric"]["correct_answers"] == ["12"]
    known = {c.id for c in ws.app.graph().concepts()}
    assert sum(t["match_status"] == "confirmed" for t in tasks) >= 4  # confident ones go live
    assert all(link["concept_id"] in known for t in tasks for link in t["links"])
    assert any(t["match_status"] == "proposed" for t in tasks)  # unsure ones wait for the expert


def test_recorded_tutor_session_replays_with_validated_evidence(rig):
    ws, space, sid = rig
    first = space.turn("me", sid, "", "explain", "gcd-definition")
    assert first["cost"]["calls"] == 1 and first["check"]["question"].startswith("What is gcd")
    second = space.turn("me", sid, WRONG, "message", "gcd-definition")
    assert second["cost"]["calls"] == 1  # grading the check answer needed no extra call
    recorded = {
        (o["target"]["id"], o["target"]["facet"], o["outcome"]) for o in second["observations"]
    }
    assert ("gcd-definition", "apply", "contradicting") in recorded
    assert all(o["judge"]["kind"] == "llm" for o in second["observations"])
    assert ws.app.notes.pending_check("me") is not None  # the tutor asked a follow-up question
    state = space.state("me")
    assert state.concepts["gcd-definition"].status in (
        BeliefStatus.LIKELY_NOT,
        BeliefStatus.UNKNOWN,
    )
    assert space.session_cost(sid)["calls"] == 2
    assert space.session_cost(sid)["input_tokens"] > 0


def test_recorded_open_answer_grading_uses_one_call(rig):
    ws, space, sid = rig
    task = next(t for t in ws.tasks() if t["title"].startswith("Explaining the GCD-LCM"))
    assert task["match_status"] == "confirmed"
    attempt = space.open_task("me", task["id"])["attempt"]["id"]
    out = space.answer_task("me", attempt, ANSWER, sid)
    assert out["graded_by"] == "llm" and out["correct"] and out["recorded"]
    assert {o["target"]["id"] for o in out["recorded"]} <= {
        link["concept_id"] for link in task["links"]
    }
    assert space.session_cost(sid)["calls"] == 1


def test_export_and_model_comparison(rig):
    _, space, sid = rig
    space.turn("me", sid, "", "explain", "gcd-definition")
    space.interact("me", "flag", "gcd-definition", {"flag": "got_it"})
    kinds = {r["type"] for r in space.export_rows("me")}
    assert {"turn", "interaction", "observation"} <= kinds
    cmp = space.compare("me", "beta-facet", "flat-beta")
    assert cmp["a"] == "beta-facet" and len(cmp["rows"]) == 66
