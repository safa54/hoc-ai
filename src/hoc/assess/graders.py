"""Grading (LEARNING_SIDE_PLAN L7.2). Exact graders cost nothing; open answers use ONE
structured call (``tutor_grade``) that also says which solution approach the learner used.

Evidence is attributed to the requirements of the approach actually used (ARCHITECTURE §7.2),
never to every approach of the task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel

from hoc.config import LearnSettings
from hoc.core.assessment import Approach, Requirement, Task, TaskType
from hoc.core.learner import Judge, JudgeKind, Observation, Outcome
from hoc.core.refs import Facet, TargetKind, TargetRef
from hoc.learner import judge_for, observation_weight
from hoc.llm import LLMClient
from hoc.store.knowledge_graph import KnowledgeGraph

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower().rstrip(".")


def parse_number(text: str) -> Fraction | None:
    """'3/4', '0.75', '1,5', '-2', '12 cm' (leading number) -> Fraction, else None."""
    cleaned = text.strip().replace(",", ".").replace("\u2212", "-")
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", cleaned)
    try:
        if m:
            return Fraction(m.group(1)) / Fraction(m.group(2))
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)", cleaned)
        return Fraction(m.group(1)) if m else None
    except (ValueError, ZeroDivisionError):
        return None


@dataclass
class GradeResult:
    correct: bool | None  # None: could not be judged (e.g. no readable number)
    feedback: str
    observations: list[Observation] = field(default_factory=list)
    approach_id: str | None = None
    llm_call_id: str | None = None
    graded_by: Literal["exact", "llm"] = "exact"


def is_exact(task: Task) -> bool:
    return task.task_type in (TaskType.MCQ, TaskType.NUMERIC) and bool(task.rubric.correct_answers)


def check_exact(task: Task, answer: str) -> bool | None:
    """True/False, or None if a numeric answer cannot be read."""
    keys = task.rubric.correct_answers
    if task.task_type is TaskType.NUMERIC:
        given = parse_number(answer)
        if given is None:
            return None
        return any((k := parse_number(c)) is not None and k == given for c in keys)
    chosen = _norm(answer)
    if len(chosen) == 1 and chosen.isalpha() and task.options:  # "b" -> second option
        index = ord(chosen) - ord("a")
        if 0 <= index < len(task.options):
            chosen = _norm(task.options[index])
    return any(chosen == _norm(c) for c in keys)


def _approach_requirements(task: Task, approach: Approach | None) -> list[tuple[str, Facet, float]]:
    chosen = approach or (task.approaches[0] if task.approaches else None)
    if chosen is None:
        return []
    return [
        (r.ref.id, r.ref.facet or Facet.APPLY, 1.0 if r.weight == "primary" else 0.5)
        for r in chosen.requirements
        if r.ref.kind is TargetKind.CONCEPT
    ]


class GradeOutcome(BaseModel):
    concept_id: str
    facet: Literal["recall", "apply", "explain", "transfer"]
    outcome: Literal["strong", "partial", "absent", "contradicting"]
    quote: str
    rationale: str


class GradeOutput(BaseModel):
    correct: bool
    approach_id: str
    outcomes: list[GradeOutcome]
    feedback: str


class Grader:
    def __init__(
        self, settings: LearnSettings, llm: LLMClient | None, graph: KnowledgeGraph
    ) -> None:
        self._s = settings
        self._llm = llm
        self._graph = graph

    def _obs(
        self,
        learner_id: str,
        task: Task,
        cid: str,
        facet: Facet,
        outcome: Outcome,
        weight: float,
        judge: Judge,
        quote: str | None,
        rationale: str,
        approach_id: str | None,
        hints: int,
    ) -> Observation:
        return Observation(
            learner_id=learner_id,
            target=TargetRef(kind=TargetKind.CONCEPT, id=cid, facet=facet),
            outcome=outcome,
            weight=weight,
            judge=judge,
            rationale=rationale,
            quote=quote,
            task_id=task.id,
            approach_id=approach_id,
            hints_used=hints,
        )

    def grade(
        self,
        learner_id: str,
        task: Task,
        answer: str,
        *,
        hints_used: int = 0,
        language: str = "English",
    ) -> GradeResult:
        if is_exact(task):
            return self._grade_exact(learner_id, task, answer, hints_used)
        return self._grade_llm(learner_id, task, answer, hints_used, language)

    # ------------------------------------------------------------------- exact

    def _grade_exact(self, learner_id: str, task: Task, answer: str, hints: int) -> GradeResult:
        verdict = check_exact(task, answer)
        if verdict is None:
            return GradeResult(None, "I couldn't read a number in your answer — try again.")
        judge = judge_for(self._s, JudgeKind.EXACT, "exact-match")
        guess = 1.0
        if task.task_type is TaskType.MCQ and len(task.options) > 1 and verdict:
            guess = 1.0 - 1.0 / len(task.options)  # chance of a lucky guess (ARCH §7.2)
        approach = task.approaches[0] if task.approaches else None
        observations = [
            self._obs(
                learner_id,
                task,
                cid,
                facet,
                Outcome.STRONG if verdict else Outcome.ABSENT,  # a wrong number may be a slip
                observation_weight(self._s, judge, requirement_weight=w * guess, hints_used=hints),
                judge,
                answer[:200],
                "Exact answer was correct" if verdict else "Exact answer was incorrect",
                approach.id if approach else None,
                hints,
            )
            for cid, facet, w in _approach_requirements(task, approach)
        ]
        feedback = "Correct!" if verdict else "Not quite. Try again or ask for a hint."
        return GradeResult(verdict, feedback, observations, approach.id if approach else None)

    # --------------------------------------------------------------------- llm

    def _grade_llm(
        self, learner_id: str, task: Task, answer: str, hints: int, language: str
    ) -> GradeResult:
        if self._llm is None:
            raise RuntimeError("an LLM client is needed to grade open answers")
        names = {c.id: c.name for c in self._graph.concepts()}

        def describe(r: Requirement) -> str:
            facet = (r.ref.facet or Facet.APPLY).value
            return f"{r.ref.id} [{names.get(r.ref.id, '?')}] facet={facet}"

        approaches = (
            "\n".join(
                f"- {a.id} ({a.label or 'approach'}): "
                + "; ".join(describe(r) for r in a.requirements)
                for a in task.approaches
            )
            or "(no approaches recorded; judge the answer overall and leave outcomes empty)"
        )
        rubric = task.rubric
        parts = [
            f"Correct answers: {'; '.join(rubric.correct_answers)}"
            if rubric.correct_answers
            else "",
            f"Expected key ideas: {'; '.join(rubric.expected_key_ideas)}"
            if rubric.expected_key_ideas
            else "",
            f"Partial credit: {rubric.partial_credit}" if rubric.partial_credit else "",
            f"Reference solution: {task.solution_text}" if task.solution_text else "",
        ]
        reference = "\n".join(x for x in parts if x) or "(none given: use your own solution)"
        result = self._llm.structured(
            "tutor_grade",
            {
                "language": language,
                "task": task.prompt,
                "approaches": approaches,
                "reference": reference,
                "answer": answer,
            },
            GradeOutput,
        )
        out = result.value
        approach = next(
            (a for a in task.approaches if a.id == out.approach_id),
            task.approaches[0] if task.approaches else None,
        )
        allowed = {cid: (facet, w) for cid, facet, w in _approach_requirements(task, approach)}
        judge = judge_for(self._s, JudgeKind.LLM, "tutor_grade")
        said = _norm(answer)
        observations: list[Observation] = []
        seen: set[str] = set()
        for o in out.outcomes:
            if o.concept_id not in allowed or o.concept_id in seen:
                continue  # only the approach's own requirements can receive evidence
            if o.outcome in ("strong", "partial", "contradicting"):
                quote = _norm(o.quote)
                if not quote or quote not in said:
                    continue  # positive or negative evidence needs a real quote
            seen.add(o.concept_id)
            _, w = allowed[o.concept_id]
            observations.append(
                self._obs(
                    learner_id,
                    task,
                    o.concept_id,
                    Facet(o.facet),
                    Outcome(o.outcome),
                    observation_weight(self._s, judge, requirement_weight=w, hints_used=hints),
                    judge,
                    answer[:200] if o.quote else None,
                    o.rationale,
                    approach.id if approach else None,
                    hints,
                )
            )
        return GradeResult(
            out.correct,
            out.feedback.strip(),
            observations,
            approach.id if approach else None,
            result.call_id,
            "llm",
        )
