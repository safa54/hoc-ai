"""Task retrieval (LEARNING_SIDE_PLAN L7.2). Deterministic, never calls an LLM."""

from __future__ import annotations

from collections.abc import Callable

from hoc.core.assessment import Task
from hoc.learner.resolve import concept_index, resolve_concept
from hoc.store.knowledge_graph import KnowledgeGraph
from hoc.store.tasks import TaskStore


class TaskBank:
    """Confirmed tasks by concept. Links to merged or split concepts are followed, so a graph
    refactoring never strands an exercise."""

    def __init__(self, tasks: TaskStore, graph: Callable[[], KnowledgeGraph] | None = None) -> None:
        self._tasks = tasks
        self._graph = graph

    def _aliases(self, concept_id: str) -> list[str]:
        ids = [concept_id]
        if self._graph is not None:
            by_id = concept_index(self._graph())
            for cid, c in by_id.items():
                retired = c.redirect_to or c.split_into
                if cid != concept_id and retired:
                    targets = [t for t, _, _ in resolve_concept(by_id, cid, 1.0)]
                    if concept_id in targets:
                        ids.append(cid)
        return ids

    def for_concept(
        self, concept_id: str, *, exclude_ids: set[str] | None = None, limit: int = 3
    ) -> list[Task]:
        exclude = exclude_ids or set()
        seen: dict[str, tuple[Task, float]] = {}
        for cid in self._aliases(concept_id):
            for task, weight in self._tasks.for_concept(cid):
                if task.id not in exclude and (task.id not in seen or weight > seen[task.id][1]):
                    seen[task.id] = (task, weight)
        ranked = sorted(seen.values(), key=lambda tw: -tw[1])  # primary targets first
        return [t for t, _ in ranked][:limit]

    def count_for_concept(self, concept_id: str) -> int:
        return len(self.for_concept(concept_id, limit=1000))

    def get(self, task_id: str) -> Task:
        return self._tasks.get(task_id)
