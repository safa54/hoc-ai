"""Triage: does a proposal go straight into the graph, or to the expert? (ARCHITECTURE §6.1)

The run mode sets the policy; the triage implementation applies it to one ChangeSet.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from hoc.config import TriageSettings
from hoc.core.build import RunMode
from hoc.core.changes import STRUCTURAL_OPS, ChangeSet
from hoc.core.errors import HocError
from hoc.store.changesets import Preview

Route = Literal["apply", "review"]


class Triage(Protocol):
    name: str

    def route(self, cs: ChangeSet, preview: Preview, mode: RunMode) -> Route: ...


class ThresholdTriage:
    """manual: everything reviewed. auto: everything applied. ask: applied unless the agent
    is unsure, the change is structural, or it touches expert-locked elements.
    Locked elements always need the expert, in every mode (ADR 0003)."""

    name = "threshold"

    def __init__(self, settings: TriageSettings) -> None:
        self._s = settings

    def route(self, cs: ChangeSet, preview: Preview, mode: RunMode) -> Route:
        if preview.touches_locked or mode is RunMode.MANUAL:
            return "review"
        if mode is RunMode.AUTO:
            return "apply"
        structural = any(op.op in STRUCTURAL_OPS or op.op == "remove_relation" for op in cs.ops)
        if self._s.review_structural and structural:
            return "review"
        return "apply" if cs.confidence >= self._s.auto_confidence else "review"


TRIAGES: dict[str, Callable[[TriageSettings], Triage]] = {"threshold": ThresholdTriage}


def create_triage(name: str, settings: TriageSettings) -> Triage:
    try:
        return TRIAGES[name](settings)
    except KeyError:
        raise HocError(f"no triage registered as {name!r}; known: {sorted(TRIAGES)}") from None
