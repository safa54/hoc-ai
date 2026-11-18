"""Evidence weight (ARCHITECTURE §7.2): reliability x requirement weight x hint discount."""

from __future__ import annotations

from hoc.config import LearnSettings
from hoc.core.learner import Judge, JudgeKind


def judge_for(settings: LearnSettings, kind: JudgeKind, judge_id: str) -> Judge:
    return Judge(
        kind=kind, id=judge_id, reliability=settings.judge_reliability.get(kind.value, 0.5)
    )


def observation_weight(
    settings: LearnSettings, judge: Judge, *, requirement_weight: float = 1.0, hints_used: int = 0
) -> float:
    discount = 1.0 / (1 + hints_used) if settings.beliefs.hint_discount else 1.0
    return round(judge.reliability * requirement_weight * discount, 4)
