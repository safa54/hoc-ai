"""Graph views ("lenses"): named, possibly overlapping subsets of concepts to look at.

A view is presentation, not knowledge (ADR 0012): it never changes the graph, it only says
which concepts to show together. Most views are *computed* from what already exists (areas,
test traces, exercises, the prerequisite structure); experts and learners may also *save*
their own. Views are connected where the graph allows: concepts added only to connect the
members are listed separately as ``bridge_ids``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from hoc.core.common import Actor, StrictModel, new_id, utc_now


class ViewKind(StrEnum):
    AREA = "area"  # a planner area (the partition)
    SUBTOPIC = "subtopic"  # a meaningful part of the topic, across areas
    QUESTION = "question"  # what a question needs (graph tests)
    EXERCISE = "exercise"  # what an exercise needs (task bank)
    OVERVIEW = "overview"  # a high-level summary of the topic
    PATH = "path"  # e.g. everything needed for one concept
    CUSTOM = "custom"


class GraphView(StrictModel):
    id: str
    title: str
    kind: ViewKind
    description: str = ""
    concept_ids: list[str] = Field(default_factory=list)  # the members
    bridge_ids: list[str] = Field(default_factory=list)  # added only to connect the members
    focus_ids: list[str] = Field(default_factory=list)  # the concepts the view is "about"
    connected: bool = True  # False if some members could not be connected
    provider: str = ""  # which provider produced it
    ref_id: str | None = None  # area / test / task / concept the view came from
    saved: bool = False
    author: Actor | None = None
    created_at: datetime | None = None

    @property
    def all_ids(self) -> list[str]:
        return [*self.concept_ids, *(b for b in self.bridge_ids if b not in self.concept_ids)]


class SavedView(StrictModel):
    """A view someone chose to keep. Stored with the topic; members are concept ids."""

    id: str = Field(default_factory=lambda: new_id("view"))
    title: str
    kind: ViewKind = ViewKind.CUSTOM
    description: str = ""
    concept_ids: list[str] = Field(default_factory=list)
    focus_ids: list[str] = Field(default_factory=list)
    author: Actor
    created_at: datetime = Field(default_factory=utc_now)
