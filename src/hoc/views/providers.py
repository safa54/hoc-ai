"""View providers: each turns what a topic already holds into a list of ``GraphView``s.

Add a provider with ``@register_view_provider("name")`` and list it under ``views.providers``
in config. A provider is a pure function ``(ViewInputs) -> list[GraphView]``; it must not
change anything. An LLM-backed provider (e.g. "agent-proposed subtopics") would store its
results as saved views and stay out of this read path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from hoc.config import ViewSettings
from hoc.core.assessment import Task
from hoc.core.build import Area, GraphTest, TestTrace
from hoc.core.errors import HocError, NotFoundError
from hoc.core.knowledge import RelationType
from hoc.core.refs import TargetKind
from hoc.core.views import GraphView, SavedView, ViewKind
from hoc.store.knowledge_graph import KnowledgeGraph
from hoc.views.connect import connect, importance, needed_for, neighbourhood


@dataclass(frozen=True)
class ViewInputs:
    graph: KnowledgeGraph
    settings: ViewSettings
    areas: list[Area] = field(default_factory=list)
    tests: list[GraphTest] = field(default_factory=list)
    traces: dict[str, TestTrace] = field(default_factory=dict)
    tasks: list[Task] = field(default_factory=list)  # confirmed exercises
    saved: list[SavedView] = field(default_factory=list)


Provider = Callable[[ViewInputs], list[GraphView]]
VIEW_PROVIDERS: dict[str, Provider] = {}


def register_view_provider(name: str) -> Callable[[Provider], Provider]:
    def deco(fn: Provider) -> Provider:
        VIEW_PROVIDERS[name] = fn
        return fn

    return deco


def _clip(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def make_view(
    inputs: ViewInputs,
    *,
    id: str,
    title: str,
    kind: ViewKind,
    concept_ids: list[str],
    provider: str,
    description: str = "",
    focus_ids: list[str] | None = None,
    ref_id: str | None = None,
    link: bool = True,
) -> GraphView:
    """Build a view, resolving merged ids and (if ``link``) connecting its members."""
    known: list[str] = []
    for cid in concept_ids:
        try:
            known.append(inputs.graph.concept(cid).id)
        except (NotFoundError, KeyError):
            continue
    if link:
        members, bridges, ok = connect(
            inputs.graph, known, max_hops=inputs.settings.max_bridge_hops
        )
    else:
        members, bridges, ok = list(dict.fromkeys(known)), [], True
    return GraphView(
        id=id,
        title=title,
        kind=kind,
        description=description,
        concept_ids=members,
        bridge_ids=bridges,
        focus_ids=[f for f in (focus_ids or []) if f in members],
        connected=ok,
        provider=provider,
        ref_id=ref_id,
    )


# ------------------------------------------------------------------ built-in providers


@register_view_provider("areas")
def area_views(inputs: ViewInputs) -> list[GraphView]:
    """The partition: one view per planner area (members as assigned, not connected)."""
    by_area: dict[str, list[str]] = {}
    for c in inputs.graph.concepts():
        if c.topic_ids:
            by_area.setdefault(c.topic_ids[0], []).append(c.id)
    return [
        make_view(
            inputs,
            id=f"area:{a.id}",
            title=a.title,
            kind=ViewKind.AREA,
            description=a.description,
            concept_ids=by_area.get(a.id, []),
            provider="areas",
            ref_id=a.id,
            link=False,
        )
        for a in inputs.areas
        if by_area.get(a.id)
    ]


@register_view_provider("questions")
def question_views(inputs: ViewInputs) -> list[GraphView]:
    """What each graph-test question walks through (its latest trace)."""
    out = []
    for t in inputs.tests:
        trace = inputs.traces.get(t.id)
        if trace is None:
            continue
        ids = list(dict.fromkeys(cid for s in trace.steps for cid in s.concept_ids))
        out.append(
            make_view(
                inputs,
                id=f"question:{t.id}",
                title=_clip(t.question),
                kind=ViewKind.QUESTION,
                description=trace.summary or t.question,
                concept_ids=ids,
                provider="questions",
                ref_id=t.id,
            )
        )
    return out


@register_view_provider("exercises")
def exercise_views(inputs: ViewInputs) -> list[GraphView]:
    """What each confirmed exercise needs (all its approaches)."""
    out = []
    for task in inputs.tasks[: inputs.settings.max_exercise_views]:
        ids = [
            r.ref.id
            for a in task.approaches
            for r in a.requirements
            if r.ref.kind is TargetKind.CONCEPT
        ]
        primary = [
            r.ref.id
            for a in task.approaches
            for r in a.requirements
            if r.ref.kind is TargetKind.CONCEPT and r.weight == "primary"
        ]
        out.append(
            make_view(
                inputs,
                id=f"exercise:{task.id}",
                title=_clip(task.title or task.prompt),
                kind=ViewKind.EXERCISE,
                description=_clip(task.prompt, 300),
                concept_ids=ids,
                focus_ids=primary,
                provider="exercises",
                ref_id=task.id,
            )
        )
    return out


@register_view_provider("overview")
def overview_views(inputs: ViewInputs) -> list[GraphView]:
    """The topic backbone: the most central concepts of each area, connected."""
    graph = inputs.graph
    score = importance(graph)
    concepts = graph.concepts()
    if not concepts:
        return []
    size = max(2, inputs.settings.overview_size)
    # take the best concepts per area round-robin so every area is represented
    by_area: dict[str, list[str]] = {}
    for c in concepts:
        by_area.setdefault(c.topic_ids[0] if c.topic_ids else "", []).append(c.id)
    for ids in by_area.values():
        ids.sort(key=lambda i: (-score.get(i, 0.0), i))
    picked: list[str] = []
    rank = 0
    while len(picked) < size and any(rank < len(v) for v in by_area.values()):
        for key in sorted(by_area):
            if rank < len(by_area[key]) and len(picked) < size:
                picked.append(by_area[key][rank])
        rank += 1
    roots = [
        c.id
        for c in concepts
        if not any(
            r.type is RelationType.PREREQUISITE_OF and r.target == c.id
            for r in graph.relations_of(c.id)
        )
    ]
    views = [
        make_view(
            inputs,
            id="overview:backbone",
            title="Topic backbone",
            kind=ViewKind.OVERVIEW,
            description="The most central concepts of each area and what connects them.",
            concept_ids=picked,
            focus_ids=picked,
            provider="overview",
        )
    ]
    if len(roots) >= inputs.settings.min_members:
        views.append(
            make_view(
                inputs,
                id="overview:starting-points",
                title="Starting points",
                kind=ViewKind.OVERVIEW,
                description="Concepts with no prerequisites inside the graph.",
                concept_ids=roots,
                provider="overview",
                link=False,
            )
        )
    return views


@register_view_provider("saved")
def saved_views(inputs: ViewInputs) -> list[GraphView]:
    out = []
    for s in inputs.saved:
        v = make_view(
            inputs,
            id=s.id,
            title=s.title,
            kind=s.kind,
            description=s.description,
            concept_ids=s.concept_ids,
            focus_ids=s.focus_ids,
            provider="saved",
            link=False,  # the author chose exactly these
        )
        out.append(
            v.model_copy(update={"saved": True, "author": s.author, "created_at": s.created_at})
        )
    return out


# ---------------------------------------------------------------- on-demand views


def needed_for_view(inputs: ViewInputs, concept_id: str) -> GraphView:
    c = inputs.graph.concept(concept_id)
    return make_view(
        inputs,
        id=f"path:{c.id}",
        title=f"Everything needed for {c.name}",
        kind=ViewKind.PATH,
        description="The concept and all its prerequisites, in learning order.",
        concept_ids=needed_for(inputs.graph, c.id),
        focus_ids=[c.id],
        provider="needed-for",
        ref_id=c.id,
    )


def around_view(inputs: ViewInputs, concept_id: str, depth: int | None = None) -> GraphView:
    c = inputs.graph.concept(concept_id)
    d = depth or inputs.settings.neighbourhood_depth
    return make_view(
        inputs,
        id=f"around:{c.id}:{d}",
        title=f"Around {c.name}",
        kind=ViewKind.PATH,
        description=f"Concepts within {d} step(s) of {c.name}.",
        concept_ids=neighbourhood(inputs.graph, c.id, d),
        focus_ids=[c.id],
        provider="around",
        ref_id=c.id,
        link=False,
    )


def compute_views(inputs: ViewInputs) -> list[GraphView]:
    """All listed views, in provider order; tiny computed views are dropped."""
    out: list[GraphView] = []
    for name in inputs.settings.providers:
        try:
            provider = VIEW_PROVIDERS[name]
        except KeyError:
            raise HocError(
                f"no view provider registered as {name!r}; known: {sorted(VIEW_PROVIDERS)}"
            ) from None
        for v in provider(inputs):
            if v.saved or len(v.concept_ids) >= inputs.settings.min_members:
                out.append(v)
    return out
