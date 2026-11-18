"""ViewService: graph views for any interface (Studio, Learn, ...). See ADR 0012.

Shares a topic's HocApp. Reading views is free (no LLM); saving one only stores which
concepts it holds. Nothing here changes the graph.
"""

from __future__ import annotations

from typing import Any

from hoc.core.build import Blueprint
from hoc.core.common import Actor
from hoc.core.errors import HocError
from hoc.core.views import GraphView, SavedView, ViewKind
from hoc.services.app import HocApp
from hoc.views import ViewInputs, around_view, compute_views, needed_for_view


class ViewService:
    def __init__(self, app: HocApp) -> None:
        self.app = app

    def _inputs(self) -> ViewInputs:
        blueprint = self.app.meta.get("blueprint", Blueprint)
        return ViewInputs(
            graph=self.app.graph(),
            settings=self.app.settings.views,
            areas=blueprint.areas if blueprint else [],
            tests=self.app.tests.all(),
            traces=self.app.tests.latest_traces(),
            tasks=self.app.task_store.all("confirmed"),
            saved=self.app.saved_views.all(),
        )

    @staticmethod
    def _json(view: GraphView) -> dict[str, Any]:
        return view.model_dump(mode="json")

    def all(self) -> list[dict[str, Any]]:
        return [self._json(v) for v in compute_views(self._inputs())]

    def needed_for(self, concept_id: str) -> dict[str, Any]:
        return self._json(needed_for_view(self._inputs(), concept_id))

    def around(self, concept_id: str, depth: int | None = None) -> dict[str, Any]:
        return self._json(around_view(self._inputs(), concept_id, depth))

    def save(
        self,
        title: str,
        concept_ids: list[str],
        *,
        kind: str = "custom",
        description: str = "",
        focus_ids: list[str] | None = None,
        author: str = "expert",
        view_id: str | None = None,
    ) -> dict[str, Any]:
        if not title.strip():
            raise HocError("a view needs a title")
        graph = self.app.graph()
        known = {c.id for c in graph.concepts()}
        ids = list(dict.fromkeys(c for c in concept_ids if c in known))
        if not ids:
            raise HocError("a view needs at least one concept of the graph")
        try:
            view_kind = ViewKind(kind)
        except ValueError:
            raise HocError(f"unknown view kind {kind!r}") from None
        fields: dict[str, Any] = {
            "title": title.strip(),
            "kind": view_kind,
            "description": description.strip(),
            "concept_ids": ids,
            "focus_ids": [f for f in (focus_ids or []) if f in ids],
            "author": Actor(kind="human", name=author or "expert"),
        }
        if view_id:
            fields["id"] = view_id
        saved = self.app.saved_views.save(SavedView(**fields))
        self.app.events.append("view.saved", {"id": saved.id, "title": saved.title})
        return {"id": saved.id}

    def delete(self, view_id: str) -> None:
        self.app.saved_views.delete(view_id)
        self.app.events.append("view.deleted", {"id": view_id})
