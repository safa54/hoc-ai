"""Named graph snapshots so sessions and evaluations can record exactly what they ran against."""

from __future__ import annotations

import json
from dataclasses import dataclass

from hoc.core.common import new_id, utc_now
from hoc.core.errors import NotFoundError
from hoc.core.knowledge import Concept, Misconception, Relation
from hoc.store.db import Database
from hoc.store.knowledge_graph import KnowledgeGraph


@dataclass(frozen=True)
class SnapshotInfo:
    id: str
    label: str
    applied_seq: int
    created_at: str


class SnapshotStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, label: str) -> SnapshotInfo:
        graph = KnowledgeGraph.load(self._db)
        payload = {
            "concepts": [c.model_dump(mode="json") for c in graph.concepts(include_inactive=True)],
            "relations": [r.model_dump(mode="json") for r in graph.relations(include_removed=True)],
            "misconceptions": [m.model_dump(mode="json") for m in graph.misconceptions()],
        }
        with self._db.transaction() as conn:
            seq = conn.execute("SELECT COALESCE(MAX(applied_seq), 0) FROM changesets").fetchone()[0]
            info = SnapshotInfo(new_id("snap"), label, int(seq), utc_now().isoformat())
            conn.execute(
                "INSERT INTO snapshots (id, label, applied_seq, created_at, data) "
                "VALUES (?,?,?,?,?)",
                (info.id, info.label, info.applied_seq, info.created_at, json.dumps(payload)),
            )
        return info

    def list(self) -> list[SnapshotInfo]:
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT id, label, applied_seq, created_at FROM snapshots ORDER BY created_at"
            ).fetchall()
        return [SnapshotInfo(r["id"], r["label"], r["applied_seq"], r["created_at"]) for r in rows]

    def load(self, snapshot_id: str) -> KnowledgeGraph:
        with self._db.read() as conn:
            row = conn.execute("SELECT data FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"snapshot {snapshot_id!r} not found")
        data = json.loads(row["data"])
        return KnowledgeGraph(
            [Concept.model_validate(c) for c in data["concepts"]],
            [Relation.model_validate(r) for r in data["relations"]],
            [Misconception.model_validate(m) for m in data["misconceptions"]],
        )
