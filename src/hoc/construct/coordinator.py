"""Coordinator: decides which agent works next. Pure functions of the workspace state, so a
run can stop at any point and continue later from the persisted ``Progress``.

The default ``PhasedCoordinator`` works in this order:
  0. tasks the expert queued explicitly (e.g. "trace this test now");
  1. plan (or re-plan after the brief, sources or directives changed);
  2. open expert feedback -> refiner (the expert always comes first);
  3. build every planned area;
  4. review rounds: critic -> write tests -> trace each test -> refiner on agent feedback.
     A round that changes nothing, or the round limit, ends the run until new input arrives.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from hoc.config import AgentSettings
from hoc.core.build import (
    AgentTask,
    Blueprint,
    Brief,
    Feedback,
    FeedbackKind,
    FeedbackStatus,
    GraphTest,
    Progress,
)
from hoc.core.errors import HocError


@dataclass(frozen=True)
class CoordinatorState:
    brief: Brief
    blueprint: Blueprint | None
    progress: Progress
    feedback: list[Feedback]
    tests: list[GraphTest]
    settings: AgentSettings
    queue: list[AgentTask]
    problem_sources: list[str] = field(default_factory=list)  # exercise sheets uploaded
    unmatched_tasks: list[str] = field(default_factory=list)  # exercises not yet linked
    covered_tests: set[str] = field(default_factory=set)  # latest trace found no gap


class Coordinator(Protocol):
    name: str

    def next_task(self, state: CoordinatorState) -> AgentTask | None: ...

    def close_round(self, state: CoordinatorState) -> Progress | None:
        """Called when ``next_task`` returns None. Return updated progress to keep going,
        or None if the run is genuinely done for now."""
        ...


def _actionable(f: Feedback, settings: AgentSettings) -> bool:
    return (
        f.status is FeedbackStatus.OPEN
        and f.kind is not FeedbackKind.QUESTION
        and f.attempts < settings.max_feedback_attempts
    )


_SEVERITY = {"low": 0, "medium": 1, "high": 2}


class PhasedCoordinator:
    name = "phased"

    def next_task(self, state: CoordinatorState) -> AgentTask | None:
        s, p = state.settings, state.progress
        if state.queue:
            return state.queue[0]
        if not state.brief.title.strip():
            return None
        if state.blueprint is None or p.blueprint_stale:
            return AgentTask(role="planner", label="Plan the topic")

        unbuilt = any(a.id not in p.built_areas for a in state.blueprint.areas)

        def refinable(f: Feedback) -> bool:
            # Standing instructions about the whole graph are consumed by the builders while
            # areas are still being built; the refiner applies them to the finished graph.
            standing = f.kind is FeedbackKind.DIRECTIVE and f.target_kind == "graph"
            return f.author.is_human and _actionable(f, s) and not (standing and unbuilt)

        expert = [f for f in state.feedback if refinable(f)]
        if expert:
            batch = expert[: s.refine_batch]
            return AgentTask(
                role="refiner",
                label=f"Act on {len(batch)} expert note(s)",
                feedback_ids=[f.id for f in batch],
            )

        for area in state.blueprint.areas:
            if area.id not in p.built_areas:
                return AgentTask(role="builder", label=f"Build '{area.title}'", area_id=area.id)

        # exercise material: read sheets, then link exercises to concepts (needs a built graph)
        unread = [x for x in state.problem_sources if x not in p.extracted_sources]
        if unread:
            return AgentTask(role="extractor", label="Read an exercise sheet", source_id=unread[0])
        if state.unmatched_tasks:
            ids = state.unmatched_tasks[:8]
            return AgentTask(
                role="matcher", label=f"Match {len(ids)} exercise(s) to concepts", task_ids=ids
            )

        if p.completed:
            return None
        if not p.critic_done:
            return AgentTask(role="critic", label=f"Review the graph (round {p.round + 1})")
        if len(state.tests) < s.min_tests and not p.tests_generated:
            return AgentTask(role="tester", label="Write test questions")
        for test in state.tests:
            if p.round > 0 and test.id in state.covered_tests and not s.retrace_covered:
                continue  # it passed last round: not worth another call
            if test.id not in p.traced:
                short = test.question if len(test.question) < 60 else test.question[:57] + "..."
                return AgentTask(role="tester", label=f"Trace: {short}", test_id=test.id)
        floor = _SEVERITY[s.refine_min_severity]
        agent_notes = [
            f
            for f in state.feedback
            if not f.author.is_human and _actionable(f, s) and _SEVERITY[f.severity] >= floor
        ]
        if agent_notes and p.refine_steps < s.max_refine_steps:
            batch = agent_notes[: s.refine_batch]
            return AgentTask(
                role="refiner",
                label=f"Resolve {len(batch)} review note(s)",
                feedback_ids=[f.id for f in batch],
            )
        return None

    def close_round(self, state: CoordinatorState) -> Progress | None:
        p = state.progress
        if p.completed or state.blueprint is None or p.blueprint_stale:
            return None
        if any(a.id not in p.built_areas for a in state.blueprint.areas):
            return None
        finished = p.round + 1
        done = p.round_activity == 0 or finished >= state.settings.max_rounds
        return p.model_copy(
            update={
                "round": finished,
                "completed": done,
                "critic_done": False,
                "traced": [],
                "refine_steps": 0,
                "round_activity": 0,
            }
        )


COORDINATORS: dict[str, Callable[[], Coordinator]] = {"phased": PhasedCoordinator}


def create_coordinator(name: str) -> Coordinator:
    try:
        return COORDINATORS[name]()
    except KeyError:
        known = sorted(COORDINATORS)
        raise HocError(f"no coordinator registered as {name!r}; known: {known}") from None
