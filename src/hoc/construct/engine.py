"""BuildEngine: runs one step of the agent team and routes everything it produces.

A step is: ask the coordinator for a task -> run the agent -> turn its output into records
(ChangeSets through triage, feedback, questions, tests, traces) -> update progress -> emit
events. The engine holds no state between steps beyond what is persisted, so steps can be
driven by a background runner, a CLI loop, a test, or one click on "step".
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from hoc.config import Settings
from hoc.construct.agents import Agent, AgentResult, Proposal, create_agent
from hoc.construct.context import BuildContext
from hoc.construct.coordinator import CoordinatorState, create_coordinator
from hoc.construct.drafts import DraftOp, compile_ops
from hoc.construct.triage import Route, create_triage
from hoc.core.assessment import Task
from hoc.core.build import (
    AgentTask,
    Blueprint,
    Brief,
    Feedback,
    FeedbackKind,
    FeedbackStatus,
    GraphDiff,
    Progress,
    RunMode,
)
from hoc.core.changes import STRUCTURAL_OPS, ChangeOrigin, ChangeSet, ChangeSetStatus
from hoc.core.common import Actor, utc_now
from hoc.core.errors import HocError
from hoc.core.provenance import EvidenceLink, Run, SourceType
from hoc.llm import LLMClient
from hoc.store import ChangeSetStore, Database, KnowledgeGraph
from hoc.store.tasks import TaskStore
from hoc.store.workspace import EventLog, FeedbackStore, MetaStore, SourceStore, TestStore

BRIEF, BLUEPRINT, PROGRESS, QUEUE = "brief", "blueprint", "progress", "queue"
_MATERIAL_TYPES = frozenset({SourceType.PROBLEM_SET.value})  # exercise sheets: not teaching text


@dataclass
class BuildStores:
    db: Database
    changesets: ChangeSetStore
    meta: MetaStore
    sources: SourceStore
    feedback: FeedbackStore
    tests: TestStore
    events: EventLog
    tasks: TaskStore


@dataclass(frozen=True)
class StepOutcome:
    kind: Literal["ran", "round", "idle"]
    summary: str = ""


def _diff_json(diff: GraphDiff) -> list[dict[str, Any]]:
    return [c.model_dump() for c in diff.changes]


class BuildEngine:
    def __init__(
        self,
        stores: BuildStores,
        settings: Settings,
        llm: Callable[[], LLMClient],
    ) -> None:
        self.s = stores
        self.settings = settings
        self._llm = llm  # lazy: only steps that call an agent need credentials
        self.coordinator = create_coordinator(settings.agents.coordinator)
        self.triage = create_triage(settings.agents.triage, settings.triage)
        self._progress_lock = threading.RLock()
        self._step_lock = threading.Lock()
        self.run_id: str | None = None

    # ------------------------------------------------------------------ state

    def brief(self) -> Brief:
        return self.s.meta.get(BRIEF, Brief) or Brief()

    def blueprint(self) -> Blueprint | None:
        return self.s.meta.get(BLUEPRINT, Blueprint)

    def progress(self) -> Progress:
        return self.s.meta.get(PROGRESS, Progress) or Progress()

    def queue(self) -> list[AgentTask]:
        raw = self.s.meta.get_raw(QUEUE) or []
        return [AgentTask.model_validate(t) for t in raw]

    def update_progress(self, fn: Callable[[Progress], Progress]) -> Progress:
        with self._progress_lock:
            new = fn(self.progress())
            self.s.meta.put(PROGRESS, new)
            return new

    def enqueue(self, task: AgentTask) -> None:
        with self._progress_lock:
            queue = self.queue()
            queue.append(task)
            self.s.meta.put_raw(QUEUE, [t.model_dump() for t in queue])

    def _pop_queue(self, task: AgentTask) -> None:
        with self._progress_lock:
            queue = self.queue()
            if queue and queue[0] == task:
                self.s.meta.put_raw(QUEUE, [t.model_dump() for t in queue[1:]])

    def start_run(self, mode: RunMode) -> str:
        run = Run(kind="graph_build", config={"mode": mode.value, **self.settings.model_dump()})
        with self.s.db.transaction() as conn:
            conn.execute(
                "INSERT INTO runs (id, data) VALUES (?, ?)", (run.id, run.model_dump_json())
            )
        self.run_id = run.id
        return run.id

    def blocked(self, mode: RunMode) -> str | None:
        """Why the team must wait for the expert, or None. Automatic mode never waits."""
        if mode is RunMode.AUTO:
            return None
        pending = self.s.changesets.find(ChangeSetStatus.PROPOSED, limit=1000)
        if pending:
            return f"{len(pending)} proposal(s) waiting for your review"
        questions = [
            f for f in self.s.feedback.find(FeedbackStatus.OPEN) if f.kind is FeedbackKind.QUESTION
        ]
        if questions:
            return f"{len(questions)} question(s) from the agents waiting for your answer"
        return None

    def _coordinator_state(self) -> CoordinatorState:
        return CoordinatorState(
            brief=self.brief(),
            blueprint=self.blueprint(),
            progress=self.progress(),
            feedback=self.s.feedback.find(),
            tests=self.s.tests.all(),
            settings=self.settings.agents,
            queue=self.queue(),
            problem_sources=[
                src.id for src in self.s.sources.all() if src.type is SourceType.PROBLEM_SET
            ],
            unmatched_tasks=[t.id for t in self.s.tasks.all() if t.match_status == "unmatched"],
            covered_tests={
                tid for tid, tr in self.s.tests.latest_traces().items() if tr.verdict == "covered"
            },
        )

    def next_task(self) -> AgentTask | None:
        return self.coordinator.next_task(self._coordinator_state())

    # ------------------------------------------------------------------- step

    def step(self, mode: RunMode) -> StepOutcome:
        with self._step_lock:
            state = self._coordinator_state()
            task = self.coordinator.next_task(state)
            if task is None:
                with self._progress_lock:
                    closed = self.coordinator.close_round(self._coordinator_state())
                    if closed is None:
                        return StepOutcome("idle", "Up to date — waiting for new input")
                    self.s.meta.put(PROGRESS, closed)
                text = (
                    f"Review round {closed.round} finished — graph is stable for now"
                    if closed.completed
                    else f"Review round {closed.round} finished — starting another"
                )
                self.s.events.append(
                    "round.closed", {"round": closed.round, "completed": closed.completed}
                )
                return StepOutcome("round", text)
            if state.queue and state.queue[0] == task:
                self._pop_queue(task)
            agent = self._agent(task.role)
            self.s.events.append(
                "agent.started",
                {
                    "role": task.role,
                    "agent": agent.name,
                    "label": task.label,
                    "task": task.model_dump(),
                },
            )
            try:
                result = agent.run(task, self._context(mode))
            except HocError as e:
                self.s.events.append(
                    "agent.error",
                    {"role": task.role, "agent": agent.name, "label": task.label, "error": str(e)},
                )
                raise
            activity = self._process(task, agent, result, mode)
            reviewed = self._reviewed(result) if task.role == "critic" else None
            self._advance(task, result, activity, reviewed)
            self.s.events.append(
                "agent.finished",
                {
                    "role": task.role,
                    "agent": agent.name,
                    "label": task.label,
                    "summary": result.summary,
                    "changes": activity,
                },
            )
            return StepOutcome("ran", result.summary)

    def _agent(self, role: str) -> Agent:
        name = self.settings.agents.team.get(role)
        if name is None:
            raise HocError(f"no agent configured for role {role!r} (agents.team)")
        return create_agent(name)

    def _context(self, mode: RunMode) -> BuildContext:
        progress = self.progress()
        return BuildContext(
            graph=KnowledgeGraph.load(self.s.db),
            brief=self.brief(),
            blueprint=self.blueprint(),
            chunks=self.s.sources.chunks(exclude_types=_MATERIAL_TYPES),
            feedback=self.s.feedback.find(),
            tests=self.s.tests.all(),
            traces=self.s.tests.latest_traces(),
            tasks=self.s.tasks.all(),
            material_chunks=[
                c
                for src in self.s.sources.all()
                if src.type is SourceType.PROBLEM_SET
                for c in self.s.sources.chunks(src.id)
            ],
            mode=mode,
            settings=self.settings.agents,
            llm=self._llm(),
            run_id=self.run_id,
            graph_seq=self.s.changesets.last_seq(),
            reviewed_seq=progress.reviewed_seq,
            reviewed_versions=progress.reviewed_versions,
        )

    # ---------------------------------------------------------------- results

    def _process(self, task: AgentTask, agent: Agent, result: AgentResult, mode: RunMode) -> int:
        actor = Actor(kind="agent", name=agent.name, version=agent.version)
        if result.blueprint is not None:
            self._save_blueprint(result.blueprint)
        for note in result.feedback:
            self.s.feedback.save(note)
            self.s.events.append("feedback.created", {"feedback": note.model_dump(mode="json")})
        for q in result.questions:
            question = Feedback.model_validate(
                {
                    "kind": FeedbackKind.QUESTION,
                    "target_kind": q.target_kind,
                    "target_id": q.target_id,
                    "text": q.text,
                    "options": q.options,
                    "author": actor,
                }
            )
            self.s.feedback.save(question)
            self.s.events.append("question.asked", {"feedback": question.model_dump(mode="json")})
        for fid, reason in result.dismissals:
            fb = self.s.feedback.resolve(fid, FeedbackStatus.DISMISSED, f"{agent.name}: {reason}")
            self.s.events.append("feedback.updated", {"feedback": fb.model_dump(mode="json")})
        for test in result.tests:
            self.s.tests.save(test)
            self.s.events.append("test.created", {"test": test.model_dump(mode="json")})
        for trace in result.traces:
            self.s.tests.add_trace(trace)
            self.s.events.append("test.traced", {"trace": trace.model_dump(mode="json")})
        for item in result.tasks:
            item = self._route_task(item, mode)
            self.s.tasks.save(item)
            self.s.events.append(
                "task.saved",
                {
                    "id": item.id,
                    "title": item.title or item.prompt[:60],
                    "status": item.match_status,
                    "confidence": item.match_confidence,
                },
            )

        activity = 0
        for proposal in result.proposals:
            if self.submit(proposal, actor, mode) is not None:
                activity += 1

        if task.role == "refiner":  # notes the refiner touched but did not resolve
            for fid in task.feedback_ids:
                fb = self.s.feedback.get(fid)
                if fb.status is FeedbackStatus.OPEN:
                    fb = self.s.feedback.save(
                        fb.model_copy(
                            update={
                                "attempts": fb.attempts + 1,
                                "resolution": fb.resolution or "refiner proposed no change",
                            }
                        )
                    )
                    self.s.events.append(
                        "feedback.updated", {"feedback": fb.model_dump(mode="json")}
                    )
        return activity

    def _route_task(self, task: Task, mode: RunMode) -> Task:
        """A matched exercise goes live by itself when the mode allows and the matcher is sure;
        otherwise it waits for the expert (same idea as triage for graph changes)."""
        sure = task.match_confidence >= self.settings.triage.auto_confidence and task.approaches
        if task.match_status == "proposed" and mode is not RunMode.MANUAL and sure:
            return task.model_copy(update={"match_status": "confirmed"})
        return task

    def _save_blueprint(self, blueprint: Blueprint) -> None:
        old = self.blueprint()
        old_ids = {a.id for a in old.areas} if old else set()
        self.s.meta.put(BLUEPRINT, blueprint)
        new_ids = [a.id for a in blueprint.areas if a.id not in old_ids]

        def fn(p: Progress) -> Progress:
            update: dict[str, Any] = {"blueprint_stale": False}
            if any(a.id not in p.built_areas for a in blueprint.areas):
                update.update(completed=False, round=0, critic_done=False, traced=[])
            return p.model_copy(update=update)

        self.update_progress(fn)
        self.s.events.append(
            "blueprint.updated",
            {
                "areas": [a.model_dump() for a in blueprint.areas],
                "new": new_ids,
                "summary": blueprint.summary,
            },
        )

    def _reviewed(self, result: AgentResult) -> tuple[int, dict[str, int]]:
        """What the critic has now seen, so its next review only reads what changed since.
        A partial review (large graph) keeps the rest pending by leaving the seq open."""
        graph = KnowledgeGraph.load(self.s.db)
        versions = {c.id: c.version for c in graph.concepts()}
        if result.reviewed is None:
            return self.s.changesets.last_seq(), versions
        seen = dict(self.progress().reviewed_versions)
        seen.update({cid: versions[cid] for cid in result.reviewed if cid in versions})
        return -1, seen

    def _advance(
        self,
        task: AgentTask,
        result: AgentResult,
        activity: int,
        reviewed: tuple[int, dict[str, int]] | None = None,
    ) -> None:
        def fn(p: Progress) -> Progress:
            update: dict[str, Any] = {"round_activity": p.round_activity + activity}
            if task.role == "builder" and task.area_id:
                update["built_areas"] = [*p.built_areas, task.area_id]
            elif task.role == "critic":
                update["critic_done"] = True
                if reviewed is not None:
                    update["reviewed_seq"], update["reviewed_versions"] = reviewed
            elif task.role == "extractor" and task.source_id:
                update["extracted_sources"] = [*p.extracted_sources, task.source_id]
            elif task.role == "tester" and task.test_id is None:
                update["tests_generated"] = True
            elif task.role == "tester" and task.test_id:
                update["traced"] = [*p.traced, task.test_id]
            elif task.role == "refiner" and not any(
                self.s.feedback.get(f).author.is_human for f in task.feedback_ids
            ):
                update["refine_steps"] = p.refine_steps + 1
            return p.model_copy(update=update)

        self.update_progress(fn)

    # -------------------------------------------------------------- proposals

    def submit(
        self,
        proposal: Proposal,
        author: Actor,
        mode: RunMode,
        *,
        origin: ChangeOrigin = ChangeOrigin.CURATOR,
        route: Route | None = None,
    ) -> ChangeSet | None:
        """Compile, dry-run, store and route one proposal. Ops that cannot apply are dropped
        and reported; the rest still goes through. Returns the stored ChangeSet, if any."""
        graph = KnowledgeGraph.load(self.s.db)
        compiled = compile_ops(proposal.drafts, graph, proposal.topic_id)
        problems = list(compiled.problems)
        ops = compiled.ops
        cs: ChangeSet | None = None
        preview = None
        for _ in range(4):
            if not ops:
                break
            cs = ChangeSet(
                author=author,
                origin=origin,
                rationale=proposal.rationale or "(no rationale given)",
                ops=ops,
                confidence=proposal.confidence,
                impact=0.8 if any(o.op in STRUCTURAL_OPS for o in ops) else 0.3,
                run_id=self.run_id,
            )
            preview = self.s.changesets.preview(cs)
            if preview.ok:
                break
            bad = {i for i, _ in preview.errors}
            problems += [f"skipped {cs.ops[i].op}: {msg}" for i, msg in preview.errors]
            ops = [op for i, op in enumerate(ops) if i not in bad]
        if cs is None or preview is None or not preview.ok or not ops:
            self.s.events.append(
                "proposal.dropped",
                {"author": author.name, "rationale": proposal.rationale, "problems": problems},
            )
            return None

        cs = cs.model_copy(
            update={
                "evidence": [
                    EvidenceLink(
                        target_kind=c.kind, target_id=c.id, llm_call_id=proposal.llm_call_id
                    )
                    for c in preview.diff.changes
                    if proposal.llm_call_id
                ]
            }
        )
        self.s.changesets.propose(cs)
        decision = route or self.triage.route(cs, preview, mode)
        self.s.events.append(
            "changeset.proposed",
            {
                "id": cs.id,
                "author": author.model_dump(),
                "rationale": cs.rationale,
                "confidence": cs.confidence,
                "diff": _diff_json(preview.diff),
                "problems": problems,
                "addresses": proposal.addresses,
                "route": decision,
            },
        )
        if decision == "apply":
            cs = self.s.changesets.apply(cs.id)
            self._applied(cs, author)
            self._resolve_addressed(proposal.addresses, cs, applied=True)
        else:
            self._resolve_addressed(proposal.addresses, cs, applied=False)
        return cs

    def _applied(self, cs: ChangeSet, by: Actor) -> None:
        diff = self.s.changesets.diff(cs.id)
        self.s.events.append(
            "changeset.applied",
            {
                "id": cs.id,
                "seq": cs.applied_seq,
                "status": cs.status.value,
                "author": cs.author.model_dump(),
                "decided_by": by.model_dump(),
                "rationale": cs.rationale,
                "diff": _diff_json(diff),
            },
        )

    def _resolve_addressed(self, ids: list[str], cs: ChangeSet, *, applied: bool) -> None:
        for fid in ids:
            fb = self.s.feedback.get(fid)
            if fb.status in (FeedbackStatus.ADDRESSED, FeedbackStatus.DISMISSED):
                continue
            if applied:
                fb = fb.model_copy(
                    update={
                        "status": FeedbackStatus.ADDRESSED,
                        "resolution": f"resolved by {cs.id}",
                        "changeset_id": cs.id,
                        "resolved_at": utc_now(),
                    }
                )
            else:
                fb = fb.model_copy(
                    update={"status": FeedbackStatus.IN_REVIEW, "changeset_id": cs.id}
                )
            self.s.feedback.save(fb)
            self.s.events.append("feedback.updated", {"feedback": fb.model_dump(mode="json")})

    # -------------------------------------------------------- expert decisions

    def decide(
        self,
        changeset_id: str,
        decision: Literal["approve", "apply", "reject"],
        expert: Actor,
        reason: str = "",
    ) -> ChangeSet:
        if decision == "reject":
            cs = self.s.changesets.reject(changeset_id, expert)
            self.s.events.append(
                "changeset.rejected", {"id": cs.id, "reason": reason, "rationale": cs.rationale}
            )
            for fb in self.s.feedback.find(FeedbackStatus.IN_REVIEW):
                if fb.changeset_id == cs.id:
                    fb = self.s.feedback.save(
                        fb.model_copy(
                            update={
                                "status": FeedbackStatus.OPEN,
                                "attempts": fb.attempts + 1,
                                "resolution": f"proposal rejected by the expert: {reason}"
                                if reason
                                else "proposal rejected by the expert",
                            }
                        )
                    )
                    self.s.events.append(
                        "feedback.updated", {"feedback": fb.model_dump(mode="json")}
                    )
            if reason.strip():
                self.add_feedback(
                    Feedback(
                        kind=FeedbackKind.COMMENT,
                        target_kind="changeset",
                        target_id=cs.id,
                        text=f'I rejected the proposal "{cs.rationale}". Reason: {reason}',
                        author=expert,
                    )
                )
            return cs
        if decision == "approve":
            cs = self.s.changesets.approve(changeset_id, expert)
        else:
            cs = self.s.changesets.apply(changeset_id, decided_by=expert)
        self._applied(cs, expert)
        for fb in self.s.feedback.find(FeedbackStatus.IN_REVIEW):
            if fb.changeset_id == cs.id:
                self._resolve_addressed([fb.id], cs, applied=True)
        return cs

    def revert(self, changeset_id: str) -> ChangeSet:
        cs = self.s.changesets.revert(changeset_id)
        self.s.events.append("changeset.reverted", {"id": cs.id, "rationale": cs.rationale})
        return cs

    def expert_change(
        self, drafts: list[DraftOp], expert: Actor, rationale: str, *, lock: bool = True
    ) -> ChangeSet:
        """A direct edit by the expert: applied at once, as an expert-authored ChangeSet."""
        cs = self.submit(
            Proposal(drafts=drafts, rationale=rationale or "Edited by the expert", confidence=1.0),
            expert,
            RunMode.MANUAL,
            origin=ChangeOrigin.EXPERT,
            route="review",
        )
        if cs is None:
            last = self.s.events.recent(1)
            problems = last[0].data.get("problems", []) if last else []
            raise HocError("edit could not be applied: " + "; ".join(problems))
        return self.decide(cs.id, "approve" if lock else "apply", expert)

    def add_feedback(self, fb: Feedback) -> Feedback:
        self.s.feedback.save(fb)
        self.s.events.append("feedback.created", {"feedback": fb.model_dump(mode="json")})
        return fb
