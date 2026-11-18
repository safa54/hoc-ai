"""TurnEngine: one learner message in, one tutor reply out, at most ONE LLM call (invariant L1.4).

``handle`` is a plain function of its inputs and the injected dependencies, so the HTTP layer,
tests and (later) a simulated learner all drive the same code path. Closed actions
(what next, flags, no-exercise replies, over-budget) cost nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from hoc.assess import TaskBank
from hoc.config import LearnSettings
from hoc.core.errors import HocError
from hoc.core.learner import Judge, JudgeKind, Observation, Outcome, Turn
from hoc.core.learning import (
    CallCost,
    Flag,
    Intent,
    Interaction,
    LearnerState,
    Move,
    PendingCheck,
    TutorReply,
)
from hoc.core.refs import Facet, TargetKind, TargetRef
from hoc.learner import judge_for, observation_weight
from hoc.llm import LLMClient
from hoc.store import (
    CallCounter,
    DecisionStore,
    InteractionStore,
    NotesStore,
    ObservationStore,
    SessionStore,
    TurnStore,
)
from hoc.store.knowledge_graph import KnowledgeGraph
from hoc.tutor.base import Tutor, TutorRequest
from hoc.tutor.budget import BudgetGuard
from hoc.tutor.context import ContextBuilder, clip
from hoc.tutor.schema import TutorTurnOutput

INTENT_LABEL = {
    Intent.EXPLAIN: "Explain this to me.",
    Intent.WHY_NEEDED: "Why do I need to know this?",
    Intent.QUIZ: "Quiz me on this.",
    Intent.EXERCISE: "Give me an exercise.",
    Intent.NEXT: "What should I do next?",
    Intent.GOT_IT: "I get it.",
    Intent.LOST: "I'm lost.",
    Intent.HINT: "Can I have a hint?",
    Intent.OVERVIEW: "Where am I, and how does this topic fit together?",
}
INTENT_NOTE = {
    Intent.MESSAGE: "",
    Intent.EXPLAIN: "The learner pressed 'Explain this': explain the focus concept simply, "
    "then ask one short check question.",
    Intent.WHY_NEEDED: "The learner pressed 'Why do I need this?': say what depends on the focus "
    "concept and why it matters, using the listed neighbours.",
    Intent.QUIZ: "The learner pressed 'Quiz me': ask ONE check question about the focus concept "
    "(set check_question, check_concept_id, check_facet, check_expected) and nothing else.",
    Intent.HINT: "The learner asked for a hint on the exercise: give a small nudge, "
    "not the answer.",
    Intent.OVERVIEW: "The learner wants the big picture: summarise the topic's structure, "
    "where they are, and what is sensible to do next, using the whole-topic map.",
}
_WS = re.compile(r"\s+")
_OUTCOME = {o.value: o for o in Outcome}


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


@dataclass
class TutorDeps:
    """Everything the engine needs, injected. No import of hoc.services (dependency direction)."""

    settings: LearnSettings
    turns: TurnStore
    sessions: SessionStore
    calls: CallCounter
    observations: ObservationStore
    interactions: InteractionStore
    decisions: DecisionStore
    notes: NotesStore
    bank: TaskBank
    tutor: Tutor
    llm: Callable[[], LLMClient]
    graph: Callable[[], KnowledgeGraph]
    state: Callable[[str], LearnerState]
    candidates: Callable[[str, str | None, LearnerState], list[Move]]
    visible: Callable[[], set[str]]
    language: Callable[[], str]
    file_gap: Callable[[str, str | None], None]
    log_decision: Callable[..., object]  # (learner, session, focus, moves, index, by) -> decision


class TurnEngine:
    def __init__(self, deps: TutorDeps) -> None:
        self.d = deps
        self.budget = BudgetGuard(deps.settings.budget, deps.calls, deps.sessions)
        self.builder = ContextBuilder(deps.settings.context)

    # ------------------------------------------------------------------ public

    def handle(
        self,
        *,
        learner_id: str,
        session_id: str,
        text: str,
        intent: str = "message",
        focus: str | None = None,
        task_text: str = "",
    ) -> TutorReply:
        d = self.d
        it = Intent(intent)
        message = text.strip() or INTENT_LABEL.get(it, "")
        if not message:
            raise HocError("say something first")
        d.turns.add(
            Turn(
                role="learner",
                text=message,
                session_id=session_id,
                learner_id=learner_id,
                focus_concept_id=focus,
                intent=it.value,
            )
        )
        before = d.state(learner_id)
        moves = d.candidates(learner_id, focus, before)
        offered = [m for m in moves if m.kind != "stay"][: d.settings.max_moves]

        if it in (Intent.NEXT, Intent.GOT_IT, Intent.LOST, Intent.EXERCISE):
            reply = self._closed(it, learner_id, session_id, focus, moves, offered, before)
        else:
            blocked = self.budget.exceeded(session_id)
            if blocked:
                reply = self._plain(
                    learner_id,
                    session_id,
                    focus,
                    blocked,
                    moves,
                    offered,
                    before,
                    meta={"budget_blocked": True},
                )
            else:
                reply = self._llm_turn(
                    it, learner_id, session_id, focus, message, moves, offered, before, task_text
                )
        return reply

    # --------------------------------------------------------- closed (no LLM)

    def _closed(
        self,
        it: Intent,
        learner_id: str,
        session_id: str,
        focus: str | None,
        moves: list[Move],
        offered: list[Move],
        before: LearnerState,
    ) -> TutorReply:
        d = self.d
        graph = d.graph()
        name = ""
        if focus:
            try:
                name = graph.concept(focus).name
            except Exception:
                name = focus
        suggestions = offered
        observations: list[Observation] = []
        if it is Intent.NEXT:
            text = (
                "Here is what I'd suggest next, based on what I've seen so far:"
                if offered
                else "You've covered everything that is ready — try the map for what's left."
            )
        elif it in (Intent.GOT_IT, Intent.LOST):
            if not focus:
                text = "Select a concept in the graph first, then tell me how it feels."
                suggestions = []
            else:
                flag = Flag.GOT_IT if it is Intent.GOT_IT else Flag.LOST
                d.interactions.add(
                    Interaction(
                        learner_id=learner_id,
                        kind="flag",
                        concept_id=focus,
                        meta={"flag": flag.value},
                    )
                )
                observations.append(self._self_report(learner_id, focus, flag))
                if flag is Flag.GOT_IT:
                    text = (
                        f'Noted: I\'ve marked **{name}** as "I get it". I count that as a weak '
                        "signal until you show it in an answer — want a quick check question?"
                    )
                else:
                    text = (
                        f"That's okay — **{name}** can be approached from a different side. "
                        "Here are some ways to back up, or just ask me about any part of it."
                    )
                    suggestions = [
                        m for m in offered if m.kind in ("review_prerequisite", "use_analogy")
                    ] or offered
        else:  # EXERCISE
            tasks = d.bank.for_concept(focus, limit=1) if focus else []
            if tasks:
                text = f"Here is an exercise on **{name}**: {tasks[0].title or 'practice'}."
                suggestions = [
                    Move(
                        kind="practice",
                        concept_id=focus,
                        concept_name=name,
                        task_id=tasks[0].id,
                        facet=Facet.APPLY,
                        reason="Exercise for this concept",
                    )
                ]
            else:
                text = (
                    "There are no exercises matched to this concept yet. "
                    "I can quiz you with a question instead."
                    if focus
                    else "Select a concept first."
                )
                suggestions = []
        return self._plain(
            learner_id,
            session_id,
            focus,
            text,
            moves,
            suggestions,
            before,
            observations=observations,
            chosen=-1,
        )

    def _self_report(self, learner_id: str, concept_id: str, flag: Flag) -> Observation:
        judge = judge_for(self.d.settings, JudgeKind.SELF_REPORT, "learner")
        got = flag is Flag.GOT_IT
        obs = Observation(
            learner_id=learner_id,
            target=TargetRef(kind=TargetKind.CONCEPT, id=concept_id, facet=Facet.RECALL),
            outcome=Outcome.STRONG if got else Outcome.CONTRADICTING,
            weight=observation_weight(self.d.settings, judge),
            judge=judge,
            rationale="Learner said they understand it" if got else "Learner said they are lost",
        )
        self.d.observations.add(obs)
        return obs

    def _plain(
        self,
        learner_id: str,
        session_id: str,
        focus: str | None,
        text: str,
        moves: list[Move],
        suggestions: list[Move],
        before: LearnerState,
        *,
        observations: list[Observation] | None = None,
        chosen: int = -1,
        meta: dict[str, object] | None = None,
    ) -> TutorReply:
        d = self.d
        decision = d.log_decision(learner_id, session_id, focus, moves, chosen, "policy")
        turn = d.turns.add(
            Turn(
                role="tutor",
                text=text,
                session_id=session_id,
                learner_id=learner_id,
                focus_concept_id=focus,
                decision_id=getattr(decision, "id", None),
                meta={
                    "cost": CallCost().model_dump(),
                    "closed": True,
                    "observation_ids": [o.id for o in observations or []],
                    **(meta or {}),
                },
            )
        )
        return TutorReply(
            turn=turn,
            suggestions=suggestions,
            decision_id=getattr(decision, "id", None),
            observations=observations or [],
            cost=CallCost(),
            changed=self._changed(learner_id, before),
        )

    # ------------------------------------------------------------- the one call

    def _llm_turn(
        self,
        it: Intent,
        learner_id: str,
        session_id: str,
        focus: str | None,
        message: str,
        moves: list[Move],
        offered: list[Move],
        before: LearnerState,
        task_text: str,
    ) -> TutorReply:
        d = self.d
        graph = d.graph()
        thread = d.turns.for_session(session_id)[:-1]  # everything before this message
        pending = d.notes.pending_check(learner_id)
        exercises = (
            [t.title or t.prompt[:50] for t in d.bank.for_concept(focus, limit=3)] if focus else []
        )
        ctx = self.builder.build(
            graph=graph,
            state=before,
            focus=focus,
            offered=offered,
            thread=thread,
            notes=d.notes.get(learner_id),
            pending=pending,
            exercises=exercises,
            task_text=task_text,
            visible=d.visible(),
        )
        request = TutorRequest(
            context=ctx.text,
            message=message,
            intent_note=INTENT_NOTE.get(it, ""),
            language=d.language(),
        )
        error = None
        out: TutorTurnOutput | None = None
        call_id = None
        try:
            response = d.tutor.respond(request)
            out, call_id = response.output, response.call_id
        except HocError as e:  # model/API failure: a graceful turn, nothing recorded as evidence
            error = str(e)
        cost = self._cost(call_id)
        if out is None:
            text = f"(The tutor model could not answer: {error})"
            return self._plain(
                learner_id,
                session_id,
                focus,
                text,
                moves,
                [],
                before,
                meta={
                    "error": error,
                    "context": ctx.text,
                    "sizes": ctx.sizes,
                    "cost": cost.model_dump(),
                },
            )

        observations = self._evidence(out, ctx.concept_ids, learner_id, message, focus, pending)
        d.observations.add_many(observations)
        check = self._update_check(out, pending, ctx.concept_ids, learner_id)
        chosen = out.move if 0 <= out.move < len(offered) else -1
        decision = d.log_decision(
            learner_id, session_id, focus, moves, chosen, "tutor" if chosen >= 0 else "policy"
        )
        turn = d.turns.add(
            Turn(
                role="tutor",
                text=out.reply.strip(),
                session_id=session_id,
                learner_id=learner_id,
                focus_concept_id=focus,
                llm_call_id=call_id,
                decision_id=getattr(decision, "id", None),
                meta={
                    "context": ctx.text,
                    "sizes": ctx.sizes,
                    "offered": [m.model_dump(mode="json") for m in offered],
                    "raw": out.model_dump(),
                    "cost": cost.model_dump(),
                    "observation_ids": [o.id for o in observations],
                    "check": check.model_dump() if check else None,
                },
            )
        )
        if check is not None:
            self.d.notes.set_pending_check(
                learner_id, check.model_copy(update={"turn_id": turn.id})
            )
        if out.note.strip():
            d.notes.append(learner_id, out.note.strip(), turn.id)
        if out.graph_gap.strip():
            d.file_gap(out.graph_gap.strip(), focus)
        return TutorReply(
            turn=turn,
            suggestions=[offered[chosen]] if chosen >= 0 else [],
            decision_id=getattr(decision, "id", None),
            observations=observations,
            check=check,
            highlight=[c for c in dict.fromkeys(out.highlight) if c in ctx.concept_ids][:12],
            cost=cost,
            changed=self._changed(learner_id, before),
        )

    # -------------------------------------------------------------- validation

    def _evidence(
        self,
        out: TutorTurnOutput,
        allowed: set[str],
        learner_id: str,
        message: str,
        focus: str | None,
        pending: PendingCheck | None,
    ) -> list[Observation]:
        """Keep only evidence the learner's own words support (invariant: evidence is strict)."""
        d = self.d
        judge = judge_for(d.settings, JudgeKind.LLM, d.tutor.id)
        weight = observation_weight(d.settings, judge)
        said = _norm(message)
        kept: list[Observation] = []
        seen: set[tuple[str, str, str]] = set()  # (concept, facet, outcome)
        for e in out.evidence:
            quote = _norm(e.quote)
            if e.concept_id not in allowed or not quote or quote not in said:
                continue  # unknown concept or a quote the learner never wrote
            key = (e.concept_id, e.facet, e.outcome)
            if key in seen:
                continue
            seen.add(key)
            kept.append(
                self._obs(
                    learner_id,
                    e.concept_id,
                    Facet(e.facet),
                    _OUTCOME[e.outcome],
                    weight,
                    judge,
                    e.rationale,
                    message,
                )
            )
        if pending is not None and out.check_result:
            verdict_key = (pending.concept_id, pending.facet.value, out.check_result)
            if verdict_key not in seen:  # the verdict may already be among the evidence items
                kept.append(
                    self._obs(
                        learner_id,
                        pending.concept_id,
                        pending.facet,
                        _OUTCOME[out.check_result],
                        weight,
                        judge,
                        f"Answer to the check question: {clip(pending.question, 120)}",
                        message,
                    )
                )
        return kept

    def _obs(
        self,
        learner_id: str,
        concept_id: str,
        facet: Facet,
        outcome: Outcome,
        weight: float,
        judge: Judge,
        rationale: str,
        message: str,
    ) -> Observation:
        return Observation(
            learner_id=learner_id,
            target=TargetRef(kind=TargetKind.CONCEPT, id=concept_id, facet=facet),
            outcome=outcome,
            weight=weight,
            judge=judge,
            rationale=rationale,
            quote=clip(message, 200),
        )

    def _update_check(
        self,
        out: TutorTurnOutput,
        pending: PendingCheck | None,
        allowed: set[str],
        learner_id: str,
    ) -> PendingCheck | None:
        """A verdict closes the pending question; a new question (valid target) replaces it."""
        if pending is not None and out.check_result:
            self.d.notes.set_pending_check(learner_id, None)
        question = out.check_question.strip()
        if question and out.check_concept_id in allowed:
            return PendingCheck(
                question=question,
                concept_id=out.check_concept_id,
                facet=Facet(out.check_facet),
                expected=[x for x in out.check_expected if x.strip()][:4],
            )
        return None

    # ---------------------------------------------------------------- helpers

    def _cost(self, call_id: str | None) -> CallCost:
        if call_id is None:
            return CallCost()
        try:
            c = self.d.llm().get_call(call_id)
        except HocError:
            return CallCost()
        return CallCost(
            model=c.model,
            input_tokens=c.input_tokens,
            output_tokens=c.output_tokens,
            usd=c.cost_usd,
            ms=c.latency_ms,
            cached=c.cached,
            calls=0 if c.cached else 1,
        )

    def _changed(self, learner_id: str, before: LearnerState) -> list[str]:
        after = self.d.state(learner_id)
        return [
            cid
            for cid, st in after.concepts.items()
            if cid not in before.concepts
            or before.concepts[cid].status != st.status
            or before.concepts[cid].self_flag != st.self_flag
        ]
