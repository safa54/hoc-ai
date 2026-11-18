"""LearningSpace: everything the Learn interface needs, over one topic (LEARNING_SIDE_PLAN).

A peer of ``TopicWorkspace`` on the same ``HocApp``. It never changes the graph: gaps noticed
while learning become ``Feedback`` for the Studio agents (invariant L1.5).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from hoc.assess import Grader, TaskBank, is_exact
from hoc.config import LearnSettings
from hoc.core.assessment import Task
from hoc.core.build import Brief, Feedback, FeedbackKind
from hoc.core.common import Actor, utc_now
from hoc.core.errors import HocError, NotFoundError
from hoc.core.knowledge import Concept, ConceptStatus, RelationType
from hoc.core.learner import JudgeKind, Learner, Observation, Outcome, PolicyDecision, Session
from hoc.core.learning import (
    CallCost,
    ConceptState,
    Flag,
    Interaction,
    LearnerState,
    Move,
    TaskAttempt,
    TutorReply,
)
from hoc.core.refs import Facet, TargetKind, TargetRef
from hoc.learner import (
    build_state,
    create_student_model,
    judge_for,
    observation_weight,
    resolve_observations,
)
from hoc.learner.state import config_hash
from hoc.policy import PolicyContext, create_policy, frontier, learning_path
from hoc.services.app import HocApp
from hoc.store.knowledge_graph import KnowledgeGraph
from hoc.tutor import TurnEngine, TutorDeps, create_tutor

_STATE_MAX_AGE = timedelta(minutes=30)


def _slug(text: str) -> str:
    from hoc.core.common import slugify

    return slugify(text)


class LearningSpace:
    def __init__(self, app: HocApp) -> None:
        self.app = app
        app.learners.ensure_default()
        self._engine: TurnEngine | None = None
        self._engine_key: tuple[str, str, str] | None = None

    # ---------------------------------------------------------------- settings

    @property
    def settings(self) -> LearnSettings:
        return self.app.settings.learn

    @property
    def bank(self) -> TaskBank:
        return TaskBank(self.app.task_store, self.app.graph)

    # ---------------------------------------------------------------- learners

    def learners(self) -> list[dict[str, Any]]:
        return [self._learner_json(x) for x in self.app.learners.all()]

    def create_learner(self, name: str, *, simulated: bool = False) -> dict[str, Any]:
        if not name.strip():
            raise HocError("a learner needs a name")
        base, n = _slug(name), 2
        lid = base
        existing = {x.id for x in self.app.learners.all()}
        while lid in existing:
            lid, n = f"{base}-{n}", n + 1
        return self._learner_json(
            self.app.learners.save(
                Learner(id=lid, display_name=name.strip(), is_simulated=simulated)
            )
        )

    def _learner_json(self, learner: Learner) -> dict[str, Any]:
        return {
            **learner.model_dump(mode="json"),
            "observations": self.app.observations.count(learner.id),
        }

    def _learner(self, learner_id: str) -> Learner:
        return self.app.learners.get(learner_id)

    # ---------------------------------------------------------------- sessions

    def session(self, learner_id: str, *, new: bool = False) -> dict[str, Any]:
        self._learner(learner_id)
        session = None if new else self.app.sessions.latest(learner_id)
        if session is None:
            session = self.app.sessions.start(learner_id)
        return {
            "session": session.model_dump(mode="json"),
            "turns": [
                t.model_dump(mode="json", exclude={"meta"})
                for t in self.app.turns.for_session(session.id)
            ],
        }

    def get_session(self, session_id: str) -> Session:
        return self.app.sessions.get(session_id)

    # ------------------------------------------------------------------- state

    def state(self, learner_id: str, model_id: str | None = None) -> LearnerState:
        """Derived state. The cache is only an optimisation: wiping it changes nothing."""
        self._learner(learner_id)
        model_id = model_id or self.settings.student_model
        graph = self.app.graph()
        seq = self.app.changesets.last_seq()
        observations = self.app.observations.for_learner(learner_id)
        interactions = self.app.interactions.for_learner(learner_id)
        cached = self.app.state_cache.get(learner_id, model_id)
        if (
            cached is not None
            and cached.graph_seq == seq
            and cached.obs_count == len(observations)
            and cached.interaction_count == len(interactions)
            and cached.config_hash == config_hash(self.settings)
            and utc_now() - cached.computed_at < _STATE_MAX_AGE
        ):
            return cached
        state = build_state(
            learner_id,
            observations,
            interactions,
            graph,
            create_student_model(model_id),
            self.settings,
            graph_seq=seq,
        )
        self.app.state_cache.put(state)
        return state

    def _visible(self, graph: KnowledgeGraph) -> list[Concept]:
        allowed = set(self.settings.visible_statuses)
        return [c for c in graph.concepts() if c.status.value in allowed]

    # ------------------------------------------------------------------- graph

    def graph(self, learner_id: str, model_id: str | None = None) -> dict[str, Any]:
        graph = self.app.graph()
        state = self.state(learner_id, model_id)
        visible = self._visible(graph)
        ids = {c.id for c in visible}
        ready = set(frontier(graph, state, self.settings))
        blueprint = self.app.meta.get_raw("blueprint")
        areas = [{"id": a["id"], "title": a["title"]} for a in (blueprint or {}).get("areas", [])]
        nodes = []
        for c in visible:
            st = state.concept(c.id)
            nodes.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "kind": c.kind.value,
                    "summary": c.card.one_line,
                    "area": c.topic_ids[0] if c.topic_ids else None,
                    "draft": c.status is ConceptStatus.PROPOSED,
                    "status": st.status.value,
                    "visited": st.visited,
                    "flag": st.self_flag.value if st.self_flag else None,
                    "ready": c.id in ready and st.status.value != "likely",
                    "inferred_mean": st.inferred_mean,
                    "n_obs": st.n_observations,
                    "tasks": self.bank.count_for_concept(c.id),
                }
            )
        edges = [
            {
                "id": r.id,
                "source": r.source,
                "target": r.target,
                "type": r.type.value,
                "necessity": r.necessity.value if r.necessity else None,
            }
            for r in graph.relations()
            if r.source in ids and r.target in ids
        ]
        counts = dict.fromkeys(("unknown", "likely_not", "uncertain", "likely", "mastered"), 0)
        for n in nodes:
            counts[str(n["status"])] += 1
        return {
            "nodes": nodes,
            "edges": edges,
            "areas": areas,
            "counts": counts,
            "total": len(nodes),
            "model": state.model_id,
            "seq": state.graph_seq,
            "collapse_threshold": self.settings.collapse_threshold,
            "path": self._path(learner_id, graph, state, ids, ready),
        }

    # -------------------------------------------------------------------- path

    def _area_order(self) -> list[str]:
        blueprint = self.app.meta.get_raw("blueprint")
        return [a["id"] for a in (blueprint or {}).get("areas", [])]

    def goal(self, learner_id: str) -> str | None:
        """The concept the learner is working towards: their latest "goal" interaction
        (a null concept clears it). Something they did in the interface, never evidence."""
        for item in reversed(self.app.interactions.for_learner(learner_id)):
            if item.kind == "goal":
                return item.concept_id
        return None

    def _path(
        self,
        learner_id: str,
        graph: KnowledgeGraph,
        state: LearnerState,
        visible: set[str],
        ready: set[str],
    ) -> dict[str, Any]:
        """The learner's route (whole topic, or towards their goal): the same order the
        policy scores "advance" moves against, so the map and the tutor agree."""
        goal = self.goal(learner_id)
        if goal is not None:
            try:
                goal = graph.resolve(goal)
            except NotFoundError:
                goal = None
        if goal not in visible:
            goal = None
        ids = learning_path(graph, goal=goal, area_order=self._area_order(), visible=visible)
        done = [c for c in ids if state.concept(c).status.understood]
        ahead = [c for c in ids if c not in set(done)]
        return {
            "goal": goal,
            "goal_name": graph.concept(goal).name if goal else None,
            "ids": ids,
            "done": done,
            "next": next((c for c in ahead if c in ready), None),
        }

    def path(self, learner_id: str) -> dict[str, Any]:
        graph = self.app.graph()
        state = self.state(learner_id)
        visible = {c.id for c in self._visible(graph)}
        ready = set(frontier(graph, state, self.settings))
        return self._path(learner_id, graph, state, visible, ready)

    # ----------------------------------------------------------------- concept

    def concept(
        self, learner_id: str, concept_id: str, model_id: str | None = None
    ) -> dict[str, Any]:
        graph = self.app.graph()
        try:
            c = graph.concept(concept_id)
        except NotFoundError:
            raise
        state = self.state(learner_id, model_id)
        model = create_student_model(model_id or self.settings.student_model)
        observations = self.app.observations.for_learner(learner_id)
        resolved = resolve_observations(observations, graph, self.settings.split_evidence_weight)
        now = utc_now()
        evidence = {
            f.value: [
                x.model_dump(mode="json")
                for x in model.explain(resolved, c.id, f, now, self.settings.beliefs)
            ]
            for f in (Facet.RECALL, Facet.APPLY, Facet.EXPLAIN, Facet.TRANSFER)
        }
        names = {x.id: x for x in graph.concepts()}

        def brief(cid: str, relation: str, necessity: str | None) -> dict[str, Any]:
            other = names.get(cid)
            st = state.concept(cid)
            return {
                "id": cid,
                "name": other.name if other else cid,
                "relation": relation,
                "necessity": necessity,
                "status": st.status.value,
                "summary": other.card.one_line if other else "",
            }

        prerequisites, leads_to, bridges = [], [], []
        for r in graph.relations_of(c.id):
            if r.type is RelationType.PREREQUISITE_OF:
                if r.target == c.id:
                    prerequisites.append(
                        brief(r.source, r.type.value, r.necessity.value if r.necessity else None)
                    )
                else:
                    leads_to.append(brief(r.target, r.type.value, None))
            else:
                other = r.target if r.source == c.id else r.source
                bridges.append(brief(other, r.type.value, None))
        st = state.concept(c.id)
        return {
            "concept": c.model_dump(mode="json"),
            "state": st.model_dump(mode="json"),
            "prerequisites": prerequisites,
            "leads_to": leads_to,
            "bridges": bridges,
            "evidence": evidence,
            "tasks": [
                {"id": t.id, "title": t.title or t.prompt[:60], "type": t.task_type.value}
                for t in self.bank.for_concept(c.id, limit=20)
            ],
        }

    # ------------------------------------------------------------ interactions

    def interact(
        self,
        learner_id: str,
        kind: str,
        concept_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._learner(learner_id)
        meta = dict(meta or {})
        if kind == "flag":
            flag = meta.get("flag")
            if flag not in (None, "got_it", "lost"):
                raise HocError("flag must be 'got_it', 'lost' or null")
            if concept_id is None:
                raise HocError("a flag needs a concept")
        if kind == "goal" and concept_id is not None:
            concept_id = self.app.graph().resolve(concept_id)  # unknown ids are an error
        if kind == "chip_accept" or kind == "chip_dismiss":
            decision_id = meta.get("decision_id")
            if decision_id:
                self.app.decisions.set_accepted(
                    decision_id, kind == "chip_accept", int(meta.get("index", -1))
                )
        item = self.app.interactions.add(
            Interaction.model_validate(
                {"learner_id": learner_id, "kind": kind, "concept_id": concept_id, "meta": meta}
            )
        )
        if kind == "flag" and meta.get("flag"):
            self.record_self_report(learner_id, concept_id or "", Flag(meta["flag"]))
        return {"interaction": item.model_dump(mode="json")}

    def record_self_report(self, learner_id: str, concept_id: str, flag: Flag) -> Observation:
        """ "I get it" / "I'm lost" is weak evidence (judge reliability 0.2) and is also shown
        as a flag, so the learner's opinion and the system's belief are visibly separate."""
        judge = judge_for(self.settings, JudgeKind.SELF_REPORT, "learner")
        got_it = flag is Flag.GOT_IT
        obs = Observation(
            learner_id=learner_id,
            target=TargetRef(kind=TargetKind.CONCEPT, id=concept_id, facet=Facet.RECALL),
            outcome=Outcome.STRONG if got_it else Outcome.CONTRADICTING,
            weight=observation_weight(self.settings, judge),
            judge=judge,
            rationale="Learner said they understand it" if got_it else "Learner said they are lost",
        )
        self.app.observations.add(obs)
        return obs

    # ------------------------------------------------------------------- moves

    def policy_context(
        self, learner_id: str, focus: str | None, state: LearnerState
    ) -> PolicyContext:
        recent = self.app.decisions.for_learner(learner_id, limit=8)
        recent_moves = [
            (m["kind"], m.get("concept_id") or m.get("task_id"))
            for d in recent
            if d.accepted is not True
            for m in d.candidates[:2]
            if m.get("kind") != "stay"
        ]
        attempts = self.app.attempts.for_learner(learner_id)
        return PolicyContext(
            state=state,
            graph=self.app.graph(),
            settings=self.settings,
            focus=focus,
            bank=self.bank,
            recent_moves=recent_moves,
            seen_task_ids={a.task_id for a in attempts},
            goal=self.goal(learner_id),
            area_order=self._area_order(),
        )

    def candidates(
        self, learner_id: str, focus: str | None, state: LearnerState | None = None
    ) -> list[Move]:
        state = state or self.state(learner_id)
        policy = create_policy(self.settings.policy)
        moves = policy.candidates(self.policy_context(learner_id, focus, state))
        for i, m in enumerate(moves):
            m.rank = i  # chips carry their index in the logged decision
        return moves

    def log_decision(
        self,
        learner_id: str,
        session_id: str | None,
        focus: str | None,
        moves: list[Move],
        chosen_index: int = -1,
        chosen_by: str = "policy",
    ) -> PolicyDecision:
        return self.app.decisions.add(
            PolicyDecision.model_validate(
                {
                    "session_id": session_id,
                    "learner_id": learner_id,
                    "focus_concept_id": focus,
                    "policy_id": self.settings.policy,
                    "candidates": [m.model_dump(mode="json") for m in moves],
                    "chosen_index": chosen_index,
                    "chosen_by": chosen_by,
                }
            )
        )

    def next_moves(
        self, learner_id: str, focus: str | None, session_id: str | None = None
    ) -> dict[str, Any]:
        """ "What next?" — answered from the policy alone, no LLM."""
        moves = self.candidates(learner_id, focus)
        offered = [m for m in moves if m.kind != "stay"][: self.settings.max_moves]
        decision = self.log_decision(learner_id, session_id, focus, moves)
        return {"decision_id": decision.id, "moves": [m.model_dump(mode="json") for m in offered]}

    # ------------------------------------------------------------------ tutor

    @property
    def engine(self) -> TurnEngine:
        """Rebuilt when the chosen tutor changes (settings are per-project overridable)."""
        key = (self.settings.tutor, self.settings.policy, str(id(self.settings)))
        if self._engine is None or self._engine_key != key:
            self._engine = TurnEngine(self._tutor_deps())
            self._engine_key = key
        return self._engine

    def _tutor_deps(self) -> TutorDeps:
        app = self.app
        return TutorDeps(
            settings=self.settings,
            turns=app.turns,
            sessions=app.sessions,
            calls=app.calls,
            observations=app.observations,
            interactions=app.interactions,
            decisions=app.decisions,
            notes=app.notes,
            bank=self.bank,
            tutor=create_tutor(self.settings.tutor, lambda: app.llm),
            llm=lambda: app.llm,
            graph=app.graph,
            state=lambda learner_id: self.state(learner_id),
            candidates=lambda learner_id, focus, state: self.candidates(learner_id, focus, state),
            visible=lambda: {c.id for c in self._visible(app.graph())},
            language=self._language,
            file_gap=self._file_gap,
            log_decision=lambda learner, session, focus, moves, index, by: self.log_decision(
                learner, session, focus, moves, index, by
            ),
        )

    def _language(self) -> str:
        brief = self.app.meta.get("brief", Brief)
        return brief.language if brief else "English"

    def _file_gap(self, text: str, concept_id: str | None) -> None:
        """The learning side never edits the graph: a gap becomes feedback for the Studio agents."""
        fb = Feedback.model_validate(
            {
                "kind": FeedbackKind.ISSUE,
                "target_kind": "concept" if concept_id else "graph",
                "target_id": concept_id,
                "severity": "medium",
                "text": f"Noticed while a learner was studying: {text}",
                "author": Actor(kind="agent", name="tutor", version="1"),
            }
        )
        self.app.feedback.save(fb)
        self.app.events.append("feedback.created", {"feedback": fb.model_dump(mode="json")})

    def turn(
        self,
        learner_id: str,
        session_id: str,
        text: str,
        intent: str = "message",
        focus: str | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session.learner_id != learner_id:
            raise HocError("that session belongs to another learner")
        reply: TutorReply = self.engine.handle(
            learner_id=learner_id, session_id=session_id, text=text, intent=intent, focus=focus
        )
        out = reply.model_dump(mode="json", exclude={"turn": {"meta"}})
        out["session_cost"] = self.session_cost(session_id)
        out["budget"] = self.engine.budget.usage(session_id)
        return out

    def session_cost(self, session_id: str) -> dict[str, Any]:
        """Calls, tokens and dollars of this session: tutor turns plus model-graded attempts.
        ``calls`` comes from the call log (the budget's source of truth)."""
        session = self.get_session(session_id)
        total = CallCost(calls=self.engine.budget.usage(session_id)["session_calls"])
        for t in self.app.turns.for_session(session_id):
            c = t.meta.get("cost") or {}
            total.input_tokens += int(c.get("input_tokens", 0))
            total.output_tokens += int(c.get("output_tokens", 0))
            total.usd += float(c.get("usd", 0.0))
        for a in self.app.attempts.for_learner(session.learner_id):
            if a.llm_call_id and a.created_at >= session.started_at:
                call = self.app.llm.get_call(a.llm_call_id)
                if not call.cached:
                    total.input_tokens += call.input_tokens
                    total.output_tokens += call.output_tokens
                    total.usd += call.cost_usd
        return total.model_dump()

    def turns(self, session_id: str) -> list[dict[str, Any]]:
        return [
            t.model_dump(mode="json", exclude={"meta"})
            for t in self.app.turns.for_session(session_id)
        ]

    def turn_debug(self, turn_id: str) -> dict[str, Any]:
        """Everything about one tutor message: what it was shown, offered, answered and cost."""
        turn = self.app.turns.get(turn_id)
        meta = dict(turn.meta)
        decision = None
        if turn.decision_id:
            decision = self.app.decisions.get(turn.decision_id).model_dump(mode="json")
        observations = [
            o.model_dump(mode="json")
            for o in self.app.observations.for_learner(turn.learner_id or "")
            if o.id in set(meta.get("observation_ids", []))
        ]
        return {
            "turn": turn.model_dump(mode="json", exclude={"meta"}),
            "meta": meta,
            "decision": decision,
            "observations": observations,
            "budget": self.engine.budget.usage(turn.session_id or ""),
        }

    # --------------------------------------------------------------- exercises

    @staticmethod
    def _public_task(task: Task) -> dict[str, Any]:
        """What the learner may see: never the answer key, rubric or solution."""
        return {
            "id": task.id,
            "title": task.title or task.prompt[:60],
            "prompt": task.prompt,
            "task_type": task.task_type.value,
            "options": task.options,
            "difficulty": task.difficulty_prior,
        }

    def _attempt_json(self, a: TaskAttempt) -> dict[str, Any]:
        return a.model_dump(mode="json", exclude={"observation_ids"})

    def tasks(self, learner_id: str, concept_id: str) -> list[dict[str, Any]]:
        attempts = self.app.attempts.for_learner(learner_id)
        out = []
        for t in self.bank.for_concept(concept_id, limit=50):
            mine = [a for a in attempts if a.task_id == t.id]
            status = "new"
            if mine:
                status = "correct" if any(a.correct for a in mine) else "attempted"
            out.append({**self._public_task(t), "status": status, "attempts": len(mine)})
        return out

    def open_task(self, learner_id: str, task_id: str) -> dict[str, Any]:
        self._learner(learner_id)
        task = self.bank.get(task_id)
        if task.match_status != "confirmed":
            raise HocError("that exercise is not available yet")
        open_attempts = [
            a
            for a in self.app.attempts.for_learner(learner_id, task_id)
            if a.correct is None and not a.revealed
        ]
        attempt = (
            open_attempts[-1]
            if open_attempts
            else self.app.attempts.save(TaskAttempt(learner_id=learner_id, task_id=task_id))
        )
        concept = next((r.ref.id for a in task.approaches for r in a.requirements), None)
        self.app.interactions.add(
            Interaction(
                learner_id=learner_id,
                kind="task_open",
                concept_id=concept,
                meta={"task_id": task_id},
            )
        )
        return {
            "task": self._public_task(task),
            "attempt": self._attempt_json(attempt),
            "concepts": self._task_concepts(task),
        }

    def _task_concepts(self, task: Task) -> list[dict[str, str]]:
        names = {c.id: c.name for c in self.app.graph().concepts()}
        seen: dict[str, str] = {}
        for a in task.approaches:
            for r in a.requirements:
                if r.ref.id in names:
                    seen[r.ref.id] = names[r.ref.id]
        return [{"id": k, "name": v} for k, v in seen.items()]

    def answer_task(
        self, learner_id: str, attempt_id: str, answer: str, session_id: str | None = None
    ) -> dict[str, Any]:
        attempt = self.app.attempts.get(attempt_id)
        if attempt.learner_id != learner_id:
            raise HocError("that attempt belongs to another learner")
        task = self.bank.get(attempt.task_id)
        if not answer.strip():
            raise HocError("write an answer first")
        before = self.state(learner_id)
        if session_id and not is_exact(task):
            blocked = self.engine.budget.exceeded(session_id)
            if blocked:
                raise HocError(blocked)
        grader = Grader(self.settings, None if is_exact(task) else self.app.llm, self.app.graph())
        result = grader.grade(
            learner_id,
            task,
            answer.strip(),
            hints_used=attempt.hints_used,
            language=self._language(),
        )
        observations = [] if attempt.revealed else result.observations
        self.app.observations.add_many(observations)
        attempt = self.app.attempts.save(
            attempt.model_copy(
                update={
                    "answer": answer.strip(),
                    "correct": result.correct,
                    "feedback": result.feedback,
                    "graded_by": result.graded_by,
                    "llm_call_id": result.llm_call_id,
                    "approach_id": result.approach_id,
                    "observation_ids": [o.id for o in observations],
                }
            )
        )
        after = self.state(learner_id)
        changed = [
            cid
            for cid, st in after.concepts.items()
            if cid in before.concepts and before.concepts[cid].status != st.status
        ]
        return {
            "attempt": self._attempt_json(attempt),
            "correct": result.correct,
            "feedback": result.feedback,
            "graded_by": result.graded_by,
            "recorded": [o.model_dump(mode="json") for o in observations],
            "evidence_skipped": attempt.revealed and bool(result.observations),
            "changed": changed,
            "solved": bool(result.correct),
        }

    def hint_task(
        self, learner_id: str, attempt_id: str, session_id: str, focus: str | None = None
    ) -> dict[str, Any]:
        attempt = self.app.attempts.get(attempt_id)
        task = self.bank.get(attempt.task_id)
        attempt = self.app.attempts.save(
            attempt.model_copy(update={"hints_used": attempt.hints_used + 1})
        )
        concepts = ", ".join(c["name"] for c in self._task_concepts(task))
        task_text = (
            f"Exercise: {task.prompt}\nConcepts it uses: {concepts or '(unknown)'}\n"
            f"Hints given so far: {attempt.hints_used - 1}\n"
            f"Learner's latest attempt: {attempt.answer or '(none yet)'}"
        )
        focus = focus or next((c["id"] for c in self._task_concepts(task)), None)
        reply = self.engine.handle(
            learner_id=learner_id,
            session_id=session_id,
            text="",
            intent="hint",
            focus=focus,
            task_text=task_text,
        )
        out = reply.model_dump(mode="json", exclude={"turn": {"meta"}})
        out["hints_used"] = attempt.hints_used
        out["session_cost"] = self.session_cost(session_id)
        return out

    def reveal_task(self, learner_id: str, attempt_id: str) -> dict[str, Any]:
        """Showing the solution is itself (weak) evidence, and ends evidence from this attempt."""
        attempt = self.app.attempts.get(attempt_id)
        task = self.bank.get(attempt.task_id)
        if not attempt.revealed:
            judge = judge_for(self.settings, JudgeKind.RULE, "solution-shown")
            approach = task.approaches[0] if task.approaches else None
            observations = [
                Observation(
                    learner_id=learner_id,
                    target=TargetRef(
                        kind=TargetKind.CONCEPT, id=r.ref.id, facet=r.ref.facet or Facet.APPLY
                    ),
                    outcome=Outcome.ABSENT,
                    weight=observation_weight(
                        self.settings, judge, requirement_weight=0.3, hints_used=attempt.hints_used
                    ),
                    judge=judge,
                    rationale="Learner asked to see the solution",
                    task_id=task.id,
                    approach_id=approach.id if approach else None,
                )
                for r in (approach.requirements if approach else [])
                if r.ref.kind is TargetKind.CONCEPT
            ]
            self.app.observations.add_many(observations)
            attempt = self.app.attempts.save(
                attempt.model_copy(
                    update={"revealed": True, "observation_ids": [o.id for o in observations]}
                )
            )
        solution = (
            task.solution_text or "; ".join(task.rubric.correct_answers) or "(no solution recorded)"
        )
        return {"attempt": self._attempt_json(attempt), "solution": solution}

    # ---------------------------------------------------------- research tables

    def compare(self, learner_id: str, model_a: str, model_b: str) -> dict[str, Any]:
        """Same evidence, two student models: which concepts do they disagree about?"""
        a = self.state(learner_id, model_a)
        b = self.state(learner_id, model_b)
        names = {c.id: c.name for c in self._visible(self.app.graph())}
        rows = []
        for cid, name in names.items():
            sa, sb = a.concept(cid).status.value, b.concept(cid).status.value
            rows.append({"id": cid, "name": name, "a": sa, "b": sb, "differs": sa != sb})
        return {
            "a": model_a,
            "b": model_b,
            "rows": rows,
            "differences": sum(1 for r in rows if r["differs"]),
        }

    def export_rows(self, learner_id: str) -> list[dict[str, Any]]:
        """Everything recorded about one learner, one JSON object per line (for analysis)."""
        self._learner(learner_id)
        rows: list[dict[str, Any]] = []

        def add(kind: str, items: list[Any]) -> None:
            rows.extend({"type": kind, **i.model_dump(mode="json")} for i in items)

        add("turn", self.app.turns.for_learner(learner_id))
        add("observation", self.app.observations.for_learner(learner_id))
        add("decision", self.app.decisions.for_learner(learner_id))
        add("interaction", self.app.interactions.for_learner(learner_id))
        add("attempt", self.app.attempts.for_learner(learner_id))
        rows.sort(key=lambda r: str(r.get("timestamp") or r.get("at") or r.get("created_at")))
        return rows

    def observations(self, learner_id: str) -> list[dict[str, Any]]:
        return [o.model_dump(mode="json") for o in self.app.observations.for_learner(learner_id)]

    def decisions(self, learner_id: str) -> list[dict[str, Any]]:
        return [d.model_dump(mode="json") for d in self.app.decisions.for_learner(learner_id)]

    def interactions(self, learner_id: str) -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in self.app.interactions.for_learner(learner_id)]

    def map(self, learner_id: str, model_id: str | None = None) -> dict[str, Any]:
        """The "My map" table: every concept's state plus the tutor's notes."""
        graph = self.app.graph()
        state = self.state(learner_id, model_id)
        ready = set(frontier(graph, state, self.settings))
        rows = []
        for c in self._visible(graph):
            st: ConceptState = state.concept(c.id)
            rows.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "area": c.topic_ids[0] if c.topic_ids else None,
                    "status": st.status.value,
                    "visited": st.visited,
                    "flag": st.self_flag.value if st.self_flag else None,
                    "ready": c.id in ready,
                    "n_obs": st.n_observations,
                    "inferred_mean": st.inferred_mean,
                    "facets": {
                        f: {"status": b.status.value, "mean": b.mean, "mass": b.evidence_mass}
                        for f, b in st.facets.items()
                    },
                    "last_evidence_at": st.last_evidence_at.isoformat()
                    if st.last_evidence_at
                    else None,
                }
            )
        notes = self.app.notes.get(learner_id)
        return {
            "rows": rows,
            "model": state.model_id,
            "models": sorted(__import__("hoc.learner", fromlist=["STUDENT_MODELS"]).STUDENT_MODELS),
            "notes": [n.model_dump(mode="json") for n in notes.entries[-20:]],
        }
