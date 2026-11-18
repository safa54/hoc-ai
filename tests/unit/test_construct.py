"""The agent team, replayed from the recorded GCD/LCM fixture (no network)."""

import time

import pytest

from hoc.construct.coordinator import PhasedCoordinator
from hoc.construct.triage import ThresholdTriage
from hoc.core.build import AgentTask, FeedbackStatus, GraphDiff, RunMode, RunStatus
from hoc.core.changes import ChangeSet, ChangeSetStatus
from hoc.store.changesets import Preview
from tests.conftest import AGENT, concept, cs


def run_until_idle(ws, mode=RunMode.AUTO, limit=60):
    kinds = []
    for _ in range(limit):
        out = ws.engine.step(mode)
        kinds.append(out.kind)
        if out.kind == "idle":
            return kinds
    raise AssertionError("did not settle")


def test_full_automatic_build_from_recording(workspace):
    run_until_idle(workspace)
    graph = workspace.app.graph()
    assert len(graph.concepts()) >= 50 and len(graph.relations()) >= 80
    import networkx as nx

    assert nx.is_directed_acyclic_graph(graph.prerequisite_graph())
    progress = workspace.engine.progress()
    assert progress.completed and len(progress.built_areas) == len(
        workspace.engine.blueprint().areas
    )
    tests = workspace.tests()
    assert len(tests) == 4 and all(t["trace"] for t in tests)
    kinds = {e["type"] for e in workspace.events()}
    assert {"agent.started", "changeset.applied", "test.traced", "blueprint.updated"} <= kinds
    # areas became navigation topics on the concepts
    assert all(c.topic_ids for c in graph.concepts())
    # every applied ChangeSet has an agent author, rationale and an LLM call as evidence
    for item in workspace.app.changesets.applied():
        assert item.author.kind == "agent" and item.rationale


def test_manual_mode_waits_for_the_expert_and_approval_locks(workspace):
    workspace.engine.step(RunMode.MANUAL)  # planner
    workspace.engine.step(RunMode.MANUAL)  # first builder
    assert workspace.app.graph().concepts() == []
    pending = workspace.app.changesets.find(ChangeSetStatus.PROPOSED)
    assert len(pending) == 1
    assert "waiting for your review" in (workspace.engine.blocked(RunMode.MANUAL) or "")
    assert workspace.engine.blocked(RunMode.AUTO) is None

    workspace.decide(pending[0].id, "approve")
    graph = workspace.app.graph()
    assert graph.concepts() and all(c.locked for c in graph.concepts())
    assert workspace.engine.blocked(RunMode.MANUAL) is None


def test_rejecting_keeps_graph_and_records_the_reason(workspace):
    workspace.engine.step(RunMode.MANUAL)
    workspace.engine.step(RunMode.MANUAL)
    cs_id = workspace.app.changesets.find(ChangeSetStatus.PROPOSED)[0].id
    workspace.decide(cs_id, "reject", "too many nodes")
    assert workspace.app.graph().concepts() == []
    notes = workspace.feedback()
    assert any("too many nodes" in n["text"] for n in notes)


def test_expert_feedback_is_acted_on_before_agent_work(workspace):
    workspace.engine.step(RunMode.AUTO)  # planner
    workspace.engine.step(RunMode.AUTO)  # first builder
    note = workspace.add_feedback(
        "Too coarse", kind="issue", target_kind="concept", target_id="factors-multiples"
    )
    task = workspace.engine.next_task()
    assert task is not None and task.role == "refiner" and note["id"] in task.feedback_ids


def test_standing_directive_replans_then_guides_builders(workspace):
    workspace.engine.step(RunMode.AUTO)  # planner
    directive = workspace.add_feedback("Keep it short", kind="directive")
    assert workspace.engine.next_task().role == "planner"  # a directive may change the plan
    workspace.engine.step(RunMode.AUTO)
    task = workspace.engine.next_task()
    assert task.role == "builder"  # builders read directives; the refiner waits for areas
    assert directive["id"] not in task.feedback_ids


def test_expert_edit_applies_locks_and_is_attributed(workspace):
    ops = [
        {"op": "add_concept", "concepts": [{"name": "Hand made", "summary": "mine"}]},
    ]
    from hoc.construct.drafts import DraftOp

    result = workspace.expert_edit([DraftOp(**o) for o in ops], "by hand")
    assert result["status"] == "approved"
    c = workspace.app.graph().concept("hand-made")
    assert c.locked and c.created_by.kind == "human"
    detail = workspace.concept("hand-made")
    assert detail["history"][0]["author"]["name"] == "expert"


def test_locked_elements_always_need_review(workspace):
    from hoc.construct.drafts import DraftOp

    workspace.expert_edit(
        [DraftOp(op="add_concept", concepts=[{"name": "Pinned", "summary": "x"}])], "pin"
    )
    triage = ThresholdTriage(workspace.app.settings.triage)
    from hoc.core.changes import UpdateConceptCard

    proposal = cs(UpdateConceptCard(concept_id="pinned", patch={"one_line": "agent edit"}))
    preview = workspace.app.changesets.preview(proposal)
    assert preview.touches_locked
    assert triage.route(proposal, preview, RunMode.AUTO) == "review"


@pytest.mark.parametrize(
    ("mode", "confidence", "structural", "expected"),
    [
        (RunMode.MANUAL, 0.99, False, "review"),
        (RunMode.AUTO, 0.1, True, "apply"),
        (RunMode.ASK, 0.9, False, "apply"),
        (RunMode.ASK, 0.5, False, "review"),
        (RunMode.ASK, 0.99, True, "review"),
    ],
)
def test_triage_matrix(mode, confidence, structural, expected):
    from hoc.config import TriageSettings
    from hoc.core.changes import DeprecateConcept

    op = DeprecateConcept(concept_id="a") if structural else concept("a")
    proposal = ChangeSet(author=AGENT, rationale="r", ops=[op], confidence=confidence)
    triage = ThresholdTriage(TriageSettings())
    assert triage.route(proposal, Preview(diff=GraphDiff()), mode) == expected


def test_trace_gaps_become_feedback_and_queue_wins(workspace):
    run_until_idle(workspace)
    test = workspace.tests()[0]
    workspace.trace(test["id"])
    task = workspace.engine.next_task()
    assert task == AgentTask(role="tester", label=task.label, test_id=test["id"])


def test_coordinator_stops_when_nothing_changes(workspace):
    run_until_idle(workspace)
    state = workspace.engine._coordinator_state()
    assert PhasedCoordinator().next_task(state) is None
    assert workspace.engine.step(RunMode.AUTO).kind == "idle"


def test_runner_pause_resume_and_status(workspace):
    runner = workspace.runner
    runner.start(RunMode.AUTO)
    runner.pause()
    deadline = time.time() + 20
    while runner.state.status is not RunStatus.PAUSED and time.time() < deadline:
        time.sleep(0.05)
    assert runner.state.status is RunStatus.PAUSED
    frozen = workspace.app.changesets.last_seq()
    time.sleep(0.4)
    assert workspace.app.changesets.last_seq() == frozen  # nothing happens while paused
    runner.resume()
    deadline = time.time() + 60
    while runner.state.status is not RunStatus.IDLE and time.time() < deadline:
        time.sleep(0.1)
    assert runner.state.status is RunStatus.IDLE
    assert workspace.app.changesets.last_seq() > frozen
    runner.stop(timeout=5)


def test_runner_failure_pauses_with_error(tmp_path):
    from hoc.core.build import Brief
    from hoc.services import TopicWorkspace
    from tests.conftest import demo_settings

    settings = demo_settings()
    settings = settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"providers": {}})}
    )
    ws = TopicWorkspace.open(str(tmp_path / "e.db"), settings)
    ws.set_brief(Brief(title="T"))
    ws.runner.start(RunMode.AUTO)
    deadline = time.time() + 20
    while ws.runner.state.status is not RunStatus.ERROR and time.time() < deadline:
        time.sleep(0.05)
    assert ws.runner.state.status is RunStatus.ERROR and ws.runner.state.error
    ws.close()


def test_changing_brief_or_sources_replans(workspace):
    run_until_idle(workspace)
    assert workspace.engine.next_task() is None
    workspace.add_source("Syllabus", text="Cover the Euclidean algorithm in depth.")
    assert workspace.engine.progress().blueprint_stale
    task = workspace.engine.next_task()
    assert task is not None and task.role == "planner"


def test_feedback_lifecycle_and_answers(workspace):
    fb = workspace.add_feedback("Wrong level", kind="issue", target_kind="graph")
    assert workspace.feedback("open")[0]["id"] == fb["id"]
    workspace.resolve_feedback(fb["id"], "dismissed", "not needed")
    assert workspace.feedback("dismissed")
    workspace.resolve_feedback(fb["id"], "open")
    assert workspace.feedback("open")[0]["attempts"] == 0


def test_model_overrides_are_saved_per_project(workspace):
    workspace.set_models({"builder": "demo:other-model"})
    info = workspace.llm_settings()
    assert info["roles"]["builder"]["override"] == "demo:other-model"
    assert info["roles"]["critic"]["override"] is None
    workspace.set_models({"builder": None})
    assert workspace.llm_settings()["roles"]["builder"]["override"] is None
    assert FeedbackStatus.OPEN.value == "open"
