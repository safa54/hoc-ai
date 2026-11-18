"""TopicWorkspace: everything an interface needs to build and curate one topic graph.

One workspace = one SQLite file = one topic. It owns the engine (what the agents do) and the
runner (when they do it), and exposes plain-data methods for the web UI and the CLI.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import ValidationError

from hoc.config import Settings, with_overrides
from hoc.construct.drafts import DraftOp
from hoc.construct.engine import BLUEPRINT, BRIEF, BuildEngine, BuildStores
from hoc.core.assessment import Approach, Requirement, Rubric, Task, TaskType
from hoc.core.build import (
    AgentTask,
    Brief,
    Feedback,
    FeedbackKind,
    FeedbackStatus,
    GraphTest,
    Progress,
    RunMode,
    RunState,
)
from hoc.core.changes import ChangeSetStatus
from hoc.core.common import Actor
from hoc.core.errors import HocError, NotFoundError
from hoc.core.provenance import SourceType
from hoc.core.refs import Facet, TargetKind, TargetRef
from hoc.ingest import extract_text, ingest_text
from hoc.llm import usage_summary
from hoc.llm.backend import LLMBackend
from hoc.services.app import HocApp
from hoc.services.runner import Runner
from hoc.store.history import graph_at
from hoc.store.knowledge_graph import KnowledgeGraph

OVERRIDES = "settings_overrides"
EXPERT = Actor(kind="human", name="expert")
ROLE_STEPS = {
    "planner": ["agent_planner"],
    "builder": ["agent_builder"],
    "critic": ["agent_critic"],
    "tester": ["agent_tester_generate", "agent_tester_trace"],
    "refiner": ["agent_refiner"],
    "extractor": ["agent_task_extract"],
    "matcher": ["agent_task_match"],
    "tutor": ["tutor_turn"],
    "grader": ["tutor_grade"],
}


def graph_json(graph: KnowledgeGraph) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": c.id,
                "name": c.name,
                "kind": c.kind.value,
                "status": c.status.value,
                "locked": c.locked,
                "summary": c.card.one_line,
                "area": c.topic_ids[0] if c.topic_ids else None,
                "version": c.version,
            }
            for c in graph.concepts()
        ],
        "edges": [
            {
                "id": r.id,
                "source": r.source,
                "target": r.target,
                "type": r.type.value,
                "necessity": r.necessity.value if r.necessity else None,
                "status": r.status.value,
                "locked": r.locked,
                "confidence": r.confidence,
                "rationale": r.rationale,
            }
            for r in graph.relations()
        ],
    }


class TopicWorkspace:
    def __init__(self, app: HocApp) -> None:
        self.app = app
        self._base_settings = app.settings
        overrides = app.meta.get_raw(OVERRIDES) or {}
        if overrides:
            app.reset_llm(with_overrides(app.settings, overrides))
        stores = BuildStores(
            db=app.db,
            changesets=app.changesets,
            meta=app.meta,
            sources=app.sources,
            feedback=app.feedback,
            tests=app.tests,
            events=app.events,
            tasks=app.task_store,
        )
        self.engine = BuildEngine(stores, app.settings, lambda: app.llm)
        self.runner = Runner(self.engine, app.meta, app.events)

    @classmethod
    def open(
        cls, db_path: str, settings: Settings, backend: LLMBackend | None = None
    ) -> TopicWorkspace:
        return cls(HocApp.open(db_path=db_path, settings=settings, backend=backend))

    def close(self) -> None:
        self.runner.stop(timeout=2)
        self.app.close()

    # ------------------------------------------------------------- overview

    def snapshot(self) -> dict[str, Any]:
        blueprint = self.engine.blueprint()
        progress = self.engine.progress()
        feedback = self.app.feedback.find()
        pending = self.app.changesets.find(ChangeSetStatus.PROPOSED, limit=1000)
        graph = self.app.graph()
        return {
            "brief": self.engine.brief().model_dump(),
            "blueprint": blueprint.model_dump(mode="json") if blueprint else None,
            "progress": progress.model_dump(),
            "run": self.runner.state.model_dump(mode="json"),
            "next_task": t.model_dump() if (t := self.engine.next_task()) else None,
            "blocked": self.engine.blocked(self.runner.state.mode),
            "counts": {
                "concepts": len(graph.concepts()),
                "relations": len(graph.relations()),
                "pending": len(pending),
                "questions": sum(
                    f.kind is FeedbackKind.QUESTION and f.status is FeedbackStatus.OPEN
                    for f in feedback
                ),
                "open_feedback": sum(f.status is FeedbackStatus.OPEN for f in feedback),
                "tests": len(self.app.tests.all()),
                "tasks": len(self.app.task_store.all()),
                "tasks_proposed": len(self.app.task_store.all("proposed")),
                "sources": len(self.app.sources.all()),
            },
            "seq": self.app.changesets.last_seq(),
            "event_seq": self.app.events.last_seq(),
        }

    # ---------------------------------------------------------------- brief

    def set_brief(self, brief: Brief) -> Brief:
        old = self.engine.brief()
        self.app.meta.put(BRIEF, brief)
        if old != brief and self.engine.blueprint() is not None:
            self.engine.update_progress(lambda p: p.model_copy(update={"blueprint_stale": True}))
        self.app.events.append("brief.updated", {"brief": brief.model_dump()})
        self.runner.wake()
        return brief

    # -------------------------------------------------------------- sources

    def add_source(
        self,
        title: str,
        *,
        text: str | None = None,
        filename: str | None = None,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        if text is None:
            if data is None or filename is None:
                raise HocError("give either text or a file")
            text = extract_text(filename, data)
        if not text.strip():
            raise HocError("the source is empty")
        source, chunks = ingest_text(
            title or filename or "Expert notes",
            text,
            type_=SourceType.NOTES,
            path=filename,
            chunk_chars=self.app.settings.agents.chunk_chars,
        )
        self.app.sources.add(source, chunks)
        if self.engine.blueprint() is not None:
            self.engine.update_progress(lambda p: p.model_copy(update={"blueprint_stale": True}))
        self.app.events.append(
            "source.added", {"id": source.id, "title": source.title, "chunks": len(chunks)}
        )
        self.runner.wake()
        return {"id": source.id, "title": source.title, "chunks": len(chunks), "chars": len(text)}

    # ------------------------------------------------------------ exercise bank

    def add_material(
        self,
        title: str,
        *,
        text: str | None = None,
        filename: str | None = None,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        """An exercise sheet: becomes tasks (extractor) linked to concepts (matcher). It is not
        teaching text, so the planner and builders never read it."""
        if text is None:
            if data is None or filename is None:
                raise HocError("give either text or a file")
            text = extract_text(filename, data)
        if not text.strip():
            raise HocError("the material is empty")
        source, chunks = ingest_text(
            title or filename or "Exercises",
            text,
            type_=SourceType.PROBLEM_SET,
            path=filename,
            chunk_chars=self.app.settings.agents.chunk_chars,
        )
        self.app.sources.add(source, chunks)
        self.app.events.append(
            "source.added",
            {"id": source.id, "title": source.title, "chunks": len(chunks), "material": True},
        )
        self.runner.wake()
        return {"id": source.id, "title": source.title, "chunks": len(chunks)}

    def _task_json(self, task: Task, names: dict[str, str]) -> dict[str, Any]:
        reqs = [
            {
                "concept_id": r.ref.id,
                "name": names.get(r.ref.id, r.ref.id),
                "facet": (r.ref.facet or Facet.APPLY).value,
                "weight": r.weight,
                "approach": a.id,
            }
            for a in task.approaches
            for r in a.requirements
        ]
        return {**task.model_dump(mode="json"), "links": reqs}

    def tasks(self) -> list[dict[str, Any]]:
        names = {c.id: c.name for c in self.app.graph().concepts()}
        return [self._task_json(t, names) for t in self.app.task_store.all()]

    def add_task(
        self,
        prompt: str,
        *,
        title: str = "",
        solution: str = "",
        task_type: str = "short_answer",
        options: list[str] | None = None,
        correct_answers: list[str] | None = None,
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise HocError("the exercise is empty")
        task = Task(
            title=title.strip(),
            prompt=prompt.strip(),
            task_type=TaskType(task_type),
            solution_text=solution.strip(),
            options=[o for o in (options or []) if o.strip()],
            rubric=Rubric(correct_answers=[a for a in (correct_answers or []) if a.strip()]),
            origin="expert",
            match_status="unmatched",
        )
        self.app.task_store.save(task)
        self.app.events.append(
            "task.saved",
            {
                "id": task.id,
                "title": task.title or prompt[:60],
                "status": task.match_status,
                "confidence": 0,
            },
        )
        self.runner.wake()
        return self._task_json(task, {})

    def task_decision(
        self, task_id: str, action: str, approaches: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        task = self.app.task_store.get(task_id)
        graph_ids = {c.id for c in self.app.graph().concepts()}
        update: dict[str, Any]
        if action == "confirm":
            if not task.approaches:
                raise HocError("link at least one concept before confirming")
            update = {"match_status": "confirmed"}
        elif action == "reject":
            update = {"match_status": "rejected"}
        elif action == "rematch":
            update = {"match_status": "unmatched", "approaches": [], "match_confidence": 0.0}
        elif action == "edit":
            built: list[Approach] = []
            for i, a in enumerate(approaches or [], 1):
                reqs = [
                    Requirement(
                        ref=TargetRef(
                            kind=TargetKind.CONCEPT,
                            id=r["concept_id"],
                            facet=Facet(r.get("facet", "apply")),
                        ),
                        weight=r.get("weight", "primary"),
                    )
                    for r in a.get("requirements", [])
                    if r.get("concept_id") in graph_ids
                ]
                if reqs:
                    built.append(Approach(id=f"a{i}", label=a.get("label", ""), requirements=reqs))
            if not built:
                raise HocError("an edit needs at least one valid concept link")
            update = {"approaches": built, "match_status": "confirmed", "match_confidence": 1.0}
        else:
            raise HocError(f"unknown action {action!r}")
        task = self.app.task_store.save(task.model_copy(update=update))
        self.app.events.append(
            "task.saved",
            {
                "id": task.id,
                "title": task.title or task.prompt[:60],
                "status": task.match_status,
                "confidence": task.match_confidence,
            },
        )
        self.runner.wake()
        names = {c.id: c.name for c in self.app.graph().concepts()}
        return self._task_json(task, names)

    def sources(self) -> list[dict[str, Any]]:
        out = []
        for s in self.app.sources.all():
            chunks = self.app.sources.chunks(s.id)
            out.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "path": s.path,
                    "type": s.type.value,
                    "chunks": len(chunks),
                    "chars": sum(len(c.text) for c in chunks),
                    "preview": chunks[0].text[:400] if chunks else "",
                    "created_at": s.created_at.isoformat(),
                }
            )
        return out

    def source_text(self, source_id: str) -> str:
        return "\n\n".join(c.text for c in self.app.sources.chunks(source_id))

    def remove_source(self, source_id: str) -> None:
        self.app.sources.remove(source_id)
        self.app.events.append("source.removed", {"id": source_id})

    # ---------------------------------------------------------------- graph

    def graph(self, at: int | None = None) -> dict[str, Any]:
        seq = self.app.changesets.last_seq()
        g = self.app.graph() if at is None or at >= seq else graph_at(self.app.db, at)
        data = graph_json(g)
        data["seq"] = seq if at is None else min(at, seq)
        data["latest_seq"] = seq
        blueprint = self.engine.blueprint()
        data["areas"] = (
            [{"id": a.id, "title": a.title} for a in blueprint.areas] if blueprint else []
        )
        return data

    def concept(self, concept_id: str) -> dict[str, Any]:
        graph = self.app.graph()
        try:
            c = graph.concept(concept_id)
        except NotFoundError:
            raise
        names = {x.id: x.name for x in graph.concepts(include_inactive=True)}
        history = []
        for cs in self.app.changesets.touching("concept", c.id):
            history.append(
                {
                    "id": cs.id,
                    "seq": cs.applied_seq,
                    "author": cs.author.model_dump(),
                    "decided_by": cs.decided_by.model_dump() if cs.decided_by else None,
                    "status": cs.status.value,
                    "rationale": cs.rationale,
                    "applied_at": cs.applied_at.isoformat() if cs.applied_at else None,
                }
            )
        traces = self.app.tests.latest_traces()
        tests = {t.id: t for t in self.app.tests.all()}
        used_by = [
            {"test_id": tid, "question": tests[tid].question, "verdict": tr.verdict}
            for tid, tr in traces.items()
            if tid in tests and any(c.id in s.concept_ids for s in tr.steps)
        ]
        return {
            "concept": c.model_dump(mode="json"),
            "relations": [
                {
                    **r.model_dump(mode="json"),
                    "source_name": names.get(r.source, r.source),
                    "target_name": names.get(r.target, r.target),
                }
                for r in graph.relations_of(c.id)
            ],
            "history": history,
            "feedback": [
                f.model_dump(mode="json")
                for f in self.app.feedback.find()
                if f.target_kind == "concept" and f.target_id == c.id
            ],
            "used_by_tests": used_by,
        }

    def relation(self, relation_id: str) -> dict[str, Any]:
        rel = next((r for r in self.app.graph().relations() if r.id == relation_id), None)
        if rel is None:
            raise NotFoundError(f"relation {relation_id!r} not found")
        return {
            "relation": rel.model_dump(mode="json"),
            "history": [
                {
                    "id": cs.id,
                    "seq": cs.applied_seq,
                    "author": cs.author.model_dump(),
                    "rationale": cs.rationale,
                    "status": cs.status.value,
                }
                for cs in self.app.changesets.touching("relation", rel.id)
            ],
            "feedback": [
                f.model_dump(mode="json")
                for f in self.app.feedback.find()
                if f.target_kind == "relation" and f.target_id == rel.id
            ],
        }

    def history(self) -> list[dict[str, Any]]:
        return [
            {
                "id": cs.id,
                "seq": cs.applied_seq,
                "author": cs.author.model_dump(),
                "rationale": cs.rationale,
                "ops": dict(Counter(op.op for op in cs.ops)),
                "status": cs.status.value,
                "applied_at": cs.applied_at.isoformat() if cs.applied_at else None,
            }
            for cs in self.app.changesets.applied()
        ]

    # ------------------------------------------------------------ proposals

    def changesets(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        st = ChangeSetStatus(status) if status else None
        out = []
        for cs in self.app.changesets.find(st, limit=limit):
            item: dict[str, Any] = {
                "id": cs.id,
                "status": cs.status.value,
                "author": cs.author.model_dump(),
                "origin": cs.origin.value,
                "rationale": cs.rationale,
                "confidence": cs.confidence,
                "impact": cs.impact,
                "seq": cs.applied_seq,
                "created_at": cs.created_at.isoformat(),
                "ops": [op.model_dump(mode="json") for op in cs.ops],
                "addresses": [f.id for f in self.app.feedback.find() if f.changeset_id == cs.id],
            }
            if cs.status is ChangeSetStatus.PROPOSED:
                preview = self.app.changesets.preview(cs)
                item["diff"] = [c.model_dump() for c in preview.diff.changes]
                item["errors"] = [m for _, m in preview.errors]
                item["touches_locked"] = preview.touches_locked
            elif cs.status.is_applied:
                item["diff"] = [c.model_dump() for c in self.app.changesets.diff(cs.id).changes]
            out.append(item)
        return out

    def decide(
        self, changeset_id: str, decision: Literal["approve", "apply", "reject"], reason: str = ""
    ) -> dict[str, Any]:
        cs = self.engine.decide(changeset_id, decision, EXPERT, reason)
        self.runner.wake()
        return {"id": cs.id, "status": cs.status.value}

    def revert(self, changeset_id: str) -> dict[str, Any]:
        cs = self.engine.revert(changeset_id)
        return {"id": cs.id, "status": cs.status.value}

    def expert_edit(
        self, ops: list[DraftOp] | list[dict[str, Any]], rationale: str, lock: bool = True
    ) -> dict[str, Any]:
        try:
            drafts = [o if isinstance(o, DraftOp) else DraftOp.model_validate(o) for o in ops]
        except ValidationError as e:
            raise HocError(f"invalid edit: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}") from e
        cs = self.engine.expert_change(drafts, EXPERT, rationale, lock=lock)
        self.runner.wake()
        return {"id": cs.id, "status": cs.status.value, "seq": cs.applied_seq}

    # ------------------------------------------------------------- feedback

    def feedback(self, status: str | None = None) -> list[dict[str, Any]]:
        st = FeedbackStatus(status) if status else None
        return [f.model_dump(mode="json") for f in self.app.feedback.find(st)]

    def add_feedback(
        self,
        text: str,
        kind: str = "comment",
        target_kind: str = "graph",
        target_id: str | None = None,
        severity: str = "medium",
    ) -> dict[str, Any]:
        if not text.strip():
            raise HocError("feedback text is empty")
        fb = Feedback.model_validate(
            {
                "kind": FeedbackKind(kind),
                "target_kind": target_kind,
                "target_id": target_id,
                "text": text.strip(),
                "severity": severity,
                "author": EXPERT,
            }
        )
        self.engine.add_feedback(fb)
        directive = fb.kind is FeedbackKind.DIRECTIVE and fb.target_kind == "graph"
        if directive and self.engine.blueprint() is not None:
            # a standing instruction may change the plan as well as the graph
            self.engine.update_progress(lambda p: p.model_copy(update={"blueprint_stale": True}))
        self.runner.wake()
        return fb.model_dump(mode="json")

    def resolve_feedback(self, feedback_id: str, status: str, note: str = "") -> dict[str, Any]:
        fb = self.app.feedback.resolve(feedback_id, FeedbackStatus(status), note)
        if fb.status is FeedbackStatus.OPEN:  # reopened: give the agents fresh attempts
            fb = self.app.feedback.save(fb.model_copy(update={"attempts": 0}))
        self.app.events.append("feedback.updated", {"feedback": fb.model_dump(mode="json")})
        self.runner.wake()
        return fb.model_dump(mode="json")

    def answer(self, question_id: str, text: str) -> dict[str, Any]:
        q = self.app.feedback.get(question_id)
        if q.kind is not FeedbackKind.QUESTION:
            raise HocError("only questions can be answered")
        answer = Feedback.model_validate(
            {
                "kind": FeedbackKind.ANSWER,
                "target_kind": q.target_kind,
                "target_id": q.target_id,
                "parent_id": q.id,
                "text": f"Question from {q.author.name}: {q.text}\nExpert's answer: {text}",
                "author": EXPERT,
            }
        )
        self.engine.add_feedback(answer)
        q = self.app.feedback.resolve(q.id, FeedbackStatus.ADDRESSED, f"answered: {text}")
        self.app.events.append("feedback.updated", {"feedback": q.model_dump(mode="json")})
        self.runner.wake()
        return answer.model_dump(mode="json")

    # ---------------------------------------------------------------- tests

    def tests(self) -> list[dict[str, Any]]:
        traces = self.app.tests.latest_traces()
        return [
            {
                **t.model_dump(mode="json"),
                "trace": traces[t.id].model_dump(mode="json") if t.id in traces else None,
            }
            for t in self.app.tests.all(active_only=False)
        ]

    def add_test(self, question: str, expected: str = "", trace_now: bool = True) -> dict[str, Any]:
        if not question.strip():
            raise HocError("the question is empty")
        test = self.app.tests.save(
            GraphTest(question=question.strip(), expected=expected.strip(), origin="expert")
        )
        self.app.events.append("test.created", {"test": test.model_dump(mode="json")})
        if trace_now:
            self.trace(test.id)
        return test.model_dump(mode="json")

    def trace(self, test_id: str) -> None:
        test = self.app.tests.get(test_id)
        short = test.question if len(test.question) < 60 else test.question[:57] + "..."
        self.engine.enqueue(AgentTask(role="tester", label=f"Trace: {short}", test_id=test.id))
        self.runner.wake()

    def set_test_active(self, test_id: str, active: bool) -> dict[str, Any]:
        test = self.app.tests.save(
            self.app.tests.get(test_id).model_copy(update={"active": active})
        )
        return test.model_dump(mode="json")

    # ------------------------------------------------------------------ run

    def run_state(self) -> RunState:
        return self.runner.state

    def control(self, action: str, mode: str | None = None) -> dict[str, Any]:
        m = RunMode(mode) if mode else None
        if action == "start":
            state = self.runner.start(m)
        elif action == "pause":
            state = self.runner.pause()
        elif action == "resume":
            state = self.runner.resume()
        elif action == "stop":
            state = self.runner.stop()
        elif action == "step":
            if m is not None:
                self.runner.set_mode(m)
            state = self.runner.step_once()
        elif action == "mode" and m is not None:
            state = self.runner.set_mode(m)
        elif action == "review":  # one more critique/test/refine round

            def reopen(p: Progress) -> Progress:
                return p.model_copy(
                    update={
                        "completed": False,
                        "critic_done": False,
                        "traced": [],
                        "refine_steps": 0,
                        "round_activity": 0,
                        "tests_generated": False,
                    }
                )

            self.engine.update_progress(reopen)
            self.runner.wake()
            state = self.runner.state
        elif action == "replan":
            self.engine.update_progress(lambda p: p.model_copy(update={"blueprint_stale": True}))
            self.runner.wake()
            state = self.runner.state
        else:
            raise HocError(f"unknown run action {action!r}")
        return state.model_dump(mode="json")

    def events(self, since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        events = self.app.events
        items = events.recent(limit) if since <= 0 else events.since(since, limit)
        return [e.model_dump(mode="json") for e in items]

    def events_after(self, seq: int) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in self.app.events.since(seq, 200)]

    # ------------------------------------------------------------ LLM setup

    def llm_settings(self) -> dict[str, Any]:
        import os

        s = self.app.settings.llm
        return {
            "providers": {
                name: {
                    "kind": p.kind,
                    "base_url": p.base_url,
                    "api_key_env": p.api_key_env,
                    "key_present": p.api_key_env is None or bool(os.environ.get(p.api_key_env)),
                    "structured_mode": p.structured_mode,
                }
                for name, p in s.providers.items()
            },
            "default_provider": s.default_provider,
            "tiers": s.models,
            "roles": {
                role: {
                    "agent": self.app.settings.agents.team.get(role),
                    "model": s.model_for(steps[0]),
                    "override": s.step_models.get(steps[0]),
                }
                for role, steps in ROLE_STEPS.items()
            },
            "usage": usage_summary(self.app.db),
        }

    def set_models(
        self, roles: dict[str, str | None], tiers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Choose a model per role (``provider:model``; empty = back to the tier default)."""
        overrides: dict[str, Any] = self.app.meta.get_raw(OVERRIDES) or {}
        llm = overrides.setdefault("llm", {})
        step_models: dict[str, str] = llm.setdefault("step_models", {})
        for role, model in roles.items():
            for step in ROLE_STEPS.get(role, []):
                if model:
                    step_models[step] = model.strip()
                else:
                    step_models.pop(step, None)
        if tiers:
            llm["models"] = {**llm.get("models", {}), **tiers}
        settings = with_overrides(self._base_settings, overrides)
        for model in settings.llm.step_models.values():
            provider, _ = settings.llm.split_model(model)
            if provider not in settings.llm.providers:
                raise HocError(f"unknown provider in {model!r}")
        self.app.meta.put_raw(OVERRIDES, overrides)
        self.app.reset_llm(settings)
        self.engine.settings = settings
        self.app.events.append("settings.updated", {"llm": llm})
        return self.llm_settings()


def default_brief(title: str, description: str = "", audience: str = "") -> Brief:
    return Brief(title=title.strip(), description=description.strip(), audience=audience.strip())


__all__ = ["BLUEPRINT", "TopicWorkspace", "default_brief", "graph_json"]
