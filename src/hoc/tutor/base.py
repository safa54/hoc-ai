"""Tutor contract: turn one request into one structured answer.

``llm`` is the default implementation (a single ``LLMClient.structured`` call). Others (rule-based,
scripted for tests, a simulated tutor) implement the same protocol and are chosen with
``learn.tutor``. The tutor never touches storage; the engine owns persistence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from hoc.core.errors import HocError
from hoc.llm import LLMClient
from hoc.tutor.schema import TutorTurnOutput


@dataclass(frozen=True)
class TutorRequest:
    context: str
    message: str
    intent_note: str
    language: str


@dataclass(frozen=True)
class TutorResponse:
    output: TutorTurnOutput
    call_id: str | None = None


class Tutor(Protocol):
    id: str

    def respond(self, request: TutorRequest) -> TutorResponse: ...


TutorFactory = Callable[[Callable[[], LLMClient]], Tutor]
TUTORS: dict[str, TutorFactory] = {}


def register_tutor[F: TutorFactory](name: str) -> Callable[[F], F]:
    def decorator(factory: F) -> F:
        TUTORS[name] = factory
        return factory

    return decorator


def create_tutor(name: str, llm: Callable[[], LLMClient]) -> Tutor:
    try:
        return TUTORS[name](llm)
    except KeyError:
        raise HocError(f"no tutor registered as {name!r}; known: {sorted(TUTORS)}") from None


@register_tutor("llm")
class LLMTutor:
    """One structured call: reply, move choice, evidence, check question and notes together."""

    id = "llm"
    step = "tutor_turn"

    def __init__(self, llm: Callable[[], LLMClient]) -> None:
        self._llm = llm

    def respond(self, request: TutorRequest) -> TutorResponse:
        result = self._llm().structured(
            self.step,
            {
                "language": request.language,
                "intent_note": request.intent_note,
                "context": request.context,
                "message": request.message,
            },
            TutorTurnOutput,
        )
        return TutorResponse(result.value, result.call_id)
