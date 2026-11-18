"""ProjectManager: one topic per SQLite file under ``projects_dir``.

Keeps one open TopicWorkspace (and so one runner) per project for the life of the process.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from hoc.config import Settings
from hoc.core.build import Brief
from hoc.core.common import slugify
from hoc.core.errors import HocError, NotFoundError
from hoc.llm.backend import LLMBackend
from hoc.services.learning import LearningSpace
from hoc.services.views import ViewService
from hoc.services.workspace import TopicWorkspace


class ProjectManager:
    def __init__(self, settings: Settings, backend: LLMBackend | None = None) -> None:
        self.settings = settings
        self.root = Path(settings.projects_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._backend = backend
        self._open: dict[str, TopicWorkspace] = {}
        self._learning: dict[str, LearningSpace] = {}
        self._lock = threading.Lock()

    def _path(self, project_id: str) -> Path:
        if slugify(project_id) != project_id:
            raise NotFoundError(f"invalid project id {project_id!r}")
        return self.root / f"{project_id}.db"

    def list(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted(self.root.glob("*.db")):
            ws = self.get(path.stem)
            snap = ws.snapshot()
            out.append(
                {
                    "id": path.stem,
                    "title": snap["brief"]["title"] or path.stem,
                    "audience": snap["brief"]["audience"],
                    "counts": snap["counts"],
                    "run": snap["run"],
                }
            )
        return out

    def create(self, brief: Brief) -> TopicWorkspace:
        if not brief.title.strip():
            raise HocError("a topic needs a title")
        base = slugify(brief.title)[:48].strip("-") or "topic"
        project_id, n = base, 2
        while self._path(project_id).exists():
            project_id, n = f"{base}-{n}", n + 1
        ws = self.get(project_id, create=True)
        ws.set_brief(brief)
        return ws

    def get(self, project_id: str, *, create: bool = False) -> TopicWorkspace:
        with self._lock:
            if project_id in self._open:
                return self._open[project_id]
            path = self._path(project_id)
            if not path.exists() and not create:
                raise NotFoundError(f"project {project_id!r} not found")
            ws = TopicWorkspace.open(str(path), self.settings, self._backend)
            self._open[project_id] = ws
            return ws

    def learning(self, project_id: str) -> LearningSpace:
        """The Learn side of a topic. Shares the workspace's HocApp (stores, LLM, settings)."""
        ws = self.get(project_id)
        with self._lock:
            if project_id not in self._learning:
                self._learning[project_id] = LearningSpace(ws.app)
            return self._learning[project_id]

    def views(self, project_id: str) -> ViewService:
        """Graph views (lenses) of a topic, shared by every interface."""
        return ViewService(self.get(project_id).app)

    def delete(self, project_id: str) -> None:
        with self._lock:
            ws = self._open.pop(project_id, None)
            self._learning.pop(project_id, None)
        if ws is not None:
            ws.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self._path(project_id)) + suffix)
            if p.exists():
                p.unlink()

    def close(self) -> None:
        with self._lock:
            items = list(self._open.values())
            self._open.clear()
            self._learning.clear()
        for ws in items:
            ws.close()
