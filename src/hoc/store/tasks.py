"""Task bank storage (assessment layer, LEARNING_SIDE_PLAN L7). Tasks are not ChangeSets: they
do not change the knowledge graph, they only point at it."""

from __future__ import annotations

from hoc.core.assessment import Task
from hoc.core.common import utc_now
from hoc.core.errors import NotFoundError
from hoc.store.db import Database


class TaskStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, task: Task) -> Task:
        """Insert or replace; the retrieval index (task_targets) is rebuilt from the document."""
        weights: dict[str, float] = {}
        for approach in task.approaches:
            for req in approach.requirements:
                if req.ref.kind.value != "concept":
                    continue
                w = 1.0 if req.weight == "primary" else 0.5
                weights[req.ref.id] = max(weights.get(req.ref.id, 0.0), w)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tasks (id, match_status, source_id, created_at, data) "
                "VALUES (?,?,?,?,?)",
                (
                    task.id,
                    task.match_status,
                    task.source_id,
                    utc_now().isoformat(),
                    task.model_dump_json(),
                ),
            )
            conn.execute("DELETE FROM task_targets WHERE task_id = ?", (task.id,))
            conn.executemany(
                "INSERT INTO task_targets (task_id, concept_id, weight) VALUES (?,?,?)",
                [(task.id, cid, w) for cid, w in weights.items()],
            )
        return task

    def get(self, task_id: str) -> Task:
        with self._db.read() as conn:
            row = conn.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"task {task_id!r} not found")
        return Task.model_validate_json(row["data"])

    def all(self, status: str | None = None) -> list[Task]:
        sql = "SELECT data FROM tasks"
        params: tuple[str, ...] = ()
        if status:
            sql += " WHERE match_status = ?"
            params = (status,)
        with self._db.read() as conn:
            rows = conn.execute(sql + " ORDER BY created_at, rowid", params).fetchall()
        return [Task.model_validate_json(r["data"]) for r in rows]

    def for_concept(self, concept_id: str) -> list[tuple[Task, float]]:
        """Confirmed tasks that need ``concept_id`` with their weight (1 primary, 0.5 secondary)."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT t.data, tt.weight FROM tasks t JOIN task_targets tt ON tt.task_id = t.id "
                "WHERE tt.concept_id = ? AND t.match_status = 'confirmed' "
                "ORDER BY tt.weight DESC, t.created_at, t.rowid",
                (concept_id,),
            ).fetchall()
        return [(Task.model_validate_json(r["data"]), float(r["weight"])) for r in rows]

    def delete_unconfirmed(self, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM task_targets WHERE task_id IN "
                "(SELECT id FROM tasks WHERE source_id = ? AND match_status != 'confirmed')",
                (source_id,),
            )
            conn.execute(
                "DELETE FROM tasks WHERE source_id = ? AND match_status != 'confirmed'",
                (source_id,),
            )
