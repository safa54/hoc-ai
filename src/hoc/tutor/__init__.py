"""The tutor: context assembly, one-call turn engine, budget guard (LEARNING_SIDE_PLAN L5)."""

from hoc.tutor.base import TUTORS, LLMTutor, Tutor, TutorRequest, TutorResponse, create_tutor
from hoc.tutor.budget import BudgetGuard
from hoc.tutor.context import ContextBuilder, TutorContext
from hoc.tutor.engine import TurnEngine, TutorDeps
from hoc.tutor.schema import TutorTurnOutput

__all__ = [
    "TUTORS",
    "BudgetGuard",
    "ContextBuilder",
    "LLMTutor",
    "TurnEngine",
    "Tutor",
    "TutorContext",
    "TutorDeps",
    "TutorRequest",
    "TutorResponse",
    "TutorTurnOutput",
    "create_tutor",
]
