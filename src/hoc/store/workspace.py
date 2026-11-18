"""Stores for the graph-building workspace: meta, sources, feedback, graph tests, events.

Small JSON-document repositories, like the rest of the store (ADR 0001).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from hoc.core.build import BuildEvent, Feedback, FeedbackStatus, GraphTest, TestTrace
from hoc.core.common import utc_now
from hoc.core.errors import NotFoundError
from hoc.core.provenance import Chunk, Source
from hoc.core.views import SavedView
from hoc.store.db import Database


class MetaStore:
    """Key -> JSON document. Holds singletons such as the brief, blueprint and run state."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get[T: BaseModel](self, key: str, model: type[T]) -> T | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else model.model_validate_json(row["value"])

    def put(self, key: str, value: BaseModel) -> None:
        self.put_raw(key, value.model_dump_json())

    def get_raw(self, key: str) -> Any:
        with self._db.read() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row["value"])

    def put_raw(self, key: str, value: Any) -> None:
        raw = value if isinstance(value, str) else json.dumps(value)
        with self._db.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, raw))

    def delete(self, key: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM meta WHERE key = ?", (key,))


class ViewStore:
    """Saved graph views (ADR 0012). Presentation data, kept as one meta document per topic."""

    KEY = "saved_views"

    def __init__(self, db: Database) -> None:
        self._meta = MetaStore(db)

    def all(self) -> list[SavedView]:
        return [SavedView.model_validate(x) for x in (self._meta.get_raw(self.KEY) or [])]

    def get(self, view_id: str) -> SavedView:
        for v in self.all():
            if v.id == view_id:
                return v
        raise NotFoundError(f"view {view_id!r} not found")

    def save(self, view: SavedView) -> SavedView:
        items = [v for v in self.all() if v.id != view.id] + [view]
        self._meta.put_raw(self.KEY, [v.model_dump(mode="json") for v in items])
        return view

    def delete(self, view_id: str) -> None:
        items = self.all()
        if not any(v.id == view_id for v in items):
            raise NotFoundError(f"view {view_id!r} not found")
        self._meta.put_raw(self.KEY, [v.model_dump(mode="json") for v in items if v.id != view_id])


class SourceStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, source: Source, chunks: list[Chunk]) -> Source:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO sources (id, created_at, data) VALUES (?, ?, ?)",
                (source.id, source.created_at.isoformat(), source.model_dump_json()),
            )
            conn.executemany(
                "INSERT INTO chunks (id, source_id, ordinal, data) VALUES (?, ?, ?, ?)",
                [(c.id, source.id, c.ordinal, c.model_dump_json()) for c in chunks],
            )
        return source

    def all(self) -> list[Source]:
        with self._db.read() as conn:
            rows = conn.execute("SELECT data FROM sources ORDER BY created_at").fetchall()
        return [Source.model_validate_json(r["data"]) for r in rows]

    def chunks(
        self,
        source_id: str | None = None,
        *,
        exclude_types: frozenset[str] = frozenset(),
    ) -> list[Chunk]:
        """Chunks in document order. ``exclude_types`` keeps e.g. exercise sheets (problem sets)
        out of what the planner and builders read as teaching material."""
        sql = "SELECT c.data, s.data AS sdata FROM chunks c JOIN sources s ON s.id = c.source_id"
        params: tuple[str, ...] = ()
        if source_id is not None:
            sql += " WHERE c.source_id = ?"
            params = (source_id,)
        with self._db.read() as conn:
            rows = conn.execute(sql + " ORDER BY s.created_at, c.ordinal", params).fetchall()
        if exclude_types:
            rows = [r for r in rows if json.loads(r["sdata"]).get("type") not in exclude_types]
        return [Chunk.model_validate_json(r["data"]) for r in rows]

    def remove(self, source_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))


class FeedbackStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, fb: Feedback) -> Feedback:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feedback (id, status, created_at, data) "
                "VALUES (?, ?, ?, ?)",
                (fb.id, fb.status.value, fb.created_at.isoformat(), fb.model_dump_json()),
            )
        return fb

    def get(self, feedback_id: str) -> Feedback:
        with self._db.read() as conn:
            row = conn.execute("SELECT data FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"feedback {feedback_id!r} not found")
        return Feedback.model_validate_json(row["data"])

    def find(self, status: FeedbackStatus | None = None) -> list[Feedback]:
        sql = "SELECT data FROM feedback"
        params: tuple[str, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        with self._db.read() as conn:
            rows = conn.execute(sql + " ORDER BY created_at, id", params).fetchall()
        return [Feedback.model_validate_json(r["data"]) for r in rows]

    def resolve(self, feedback_id: str, status: FeedbackStatus, resolution: str = "") -> Feedback:
        fb = self.get(feedback_id)
        closed = status in (FeedbackStatus.ADDRESSED, FeedbackStatus.DISMISSED)
        return self.save(
            fb.model_copy(
                update={
                    "status": status,
                    "resolution": resolution or fb.resolution,
                    "resolved_at": utc_now() if closed else None,
                }
            )
        )


class TestStore:
    __test__ = False  # not a pytest class

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, test: GraphTest) -> GraphTest:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO graph_tests (id, created_at, data) VALUES (?, ?, ?)",
                (test.id, test.created_at.isoformat(), test.model_dump_json()),
            )
        return test

    def get(self, test_id: str) -> GraphTest:
        with self._db.read() as conn:
            row = conn.execute("SELECT data FROM graph_tests WHERE id = ?", (test_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"test {test_id!r} not found")
        return GraphTest.model_validate_json(row["data"])

    def all(self, *, active_only: bool = True) -> list[GraphTest]:
        with self._db.read() as conn:
            rows = conn.execute("SELECT data FROM graph_tests ORDER BY created_at, id").fetchall()
        tests = [GraphTest.model_validate_json(r["data"]) for r in rows]
        return [t for t in tests if t.active or not active_only]

    def add_trace(self, trace: TestTrace) -> TestTrace:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO test_traces (id, test_id, created_at, data) VALUES (?, ?, ?, ?)",
                (trace.id, trace.test_id, trace.created_at.isoformat(), trace.model_dump_json()),
            )
        return trace

    def latest_traces(self) -> dict[str, TestTrace]:
        """Most recent trace per test."""
        with self._db.read() as conn:
            rows = conn.execute("SELECT data FROM test_traces ORDER BY created_at, id").fetchall()
        out: dict[str, TestTrace] = {}
        for r in rows:
            trace = TestTrace.model_validate_json(r["data"])
            out[trace.test_id] = trace
        return out


Listener = Callable[[BuildEvent], None]


class EventLog:
    """Append-only event stream with in-process listeners (the UI's live feed)."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def append(self, type_: str, data: dict[str, Any] | None = None) -> BuildEvent:
        event = BuildEvent(type=type_, data=data or {})
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO events (type, created_at, data) VALUES (?, ?, ?)",
                (event.type, event.created_at.isoformat(), json.dumps(event.data)),
            )
            event = event.model_copy(update={"seq": int(cur.lastrowid or 0)})
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(event)
        return event

    def since(self, seq: int = 0, limit: int = 500) -> list[BuildEvent]:
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT seq, type, created_at, data FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
                (seq, limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 200) -> list[BuildEvent]:
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT seq, type, created_at, data FROM events ORDER BY seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def last_seq(self) -> int:
        with self._db.read() as conn:
            return int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0])

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    @staticmethod
    def _row(r: Any) -> BuildEvent:
        return BuildEvent.model_validate(
            {
                "seq": r["seq"],
                "type": r["type"],
                "created_at": r["created_at"],
                "data": json.loads(r["data"]),
            }
        )
