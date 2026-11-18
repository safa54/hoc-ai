"""Learner modelling: derive beliefs from immutable evidence. Pure — no LLM, no I/O."""

from hoc.learner import beta_facet as _beta_facet  # noqa: F401  (registers student models)
from hoc.learner.base import (
    STUDENT_MODELS,
    ResolvedObservation,
    StudentModel,
    create_student_model,
    register_student_model,
)
from hoc.learner.resolve import resolve_concept, resolve_observations
from hoc.learner.state import build_state, concept_mean, concept_status
from hoc.learner.weights import judge_for, observation_weight

__all__ = [
    "STUDENT_MODELS",
    "ResolvedObservation",
    "StudentModel",
    "build_state",
    "concept_mean",
    "concept_status",
    "create_student_model",
    "judge_for",
    "observation_weight",
    "register_student_model",
    "resolve_concept",
    "resolve_observations",
]
