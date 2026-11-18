"""Student-model contract (ARCHITECTURE §8.1). Models are pure functions of evidence.

Add a model: implement ``StudentModel``, register it with ``@register_student_model("name")``,
select it with ``learn.student_model``. State is always recomputed from observations, so models
can be swapped and compared on the same log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hoc.config import BeliefSettings
from hoc.core.errors import HocError
from hoc.core.learner import Observation
from hoc.core.learning import Belief, BeliefContribution
from hoc.core.refs import Facet


@dataclass(frozen=True)
class ResolvedObservation:
    """An observation whose target has been mapped onto a currently active concept."""

    observation: Observation
    concept_id: str
    multiplier: float = 1.0  # e.g. split inheritance halves the weight
    inferred: bool = False


class StudentModel(Protocol):
    id: str

    def compute(
        self, observations: list[ResolvedObservation], at: datetime, settings: BeliefSettings
    ) -> dict[str, dict[Facet, Belief]]: ...

    def explain(
        self,
        observations: list[ResolvedObservation],
        concept_id: str,
        facet: Facet | None,
        at: datetime,
        settings: BeliefSettings,
    ) -> list[BeliefContribution]: ...


StudentModelFactory = Callable[[], StudentModel]
STUDENT_MODELS: dict[str, StudentModelFactory] = {}


def register_student_model[F: StudentModelFactory](name: str) -> Callable[[F], F]:
    def decorator(factory: F) -> F:
        STUDENT_MODELS[name] = factory
        return factory

    return decorator


def create_student_model(name: str) -> StudentModel:
    try:
        return STUDENT_MODELS[name]()
    except KeyError:
        raise HocError(
            f"no student model registered as {name!r}; known: {sorted(STUDENT_MODELS)}"
        ) from None
