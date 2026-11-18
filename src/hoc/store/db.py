"""SQLite access. Entities are stored as validated JSON documents plus a few indexed columns.

See docs/adr/0001-sqlite-document-store.md for why this is not an ORM.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL,
    status TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_rel_source ON relations(source);
CREATE INDEX IF NOT EXISTS ix_rel_target ON relations(target);
CREATE TABLE IF NOT EXISTS misconceptions (id TEXT PRIMARY KEY, data TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS changesets (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, applied_seq INTEGER,
    created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cs_status ON changesets(status);
CREATE TABLE IF NOT EXISTS changeset_touch (
    changeset_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL, entity_id TEXT NOT NULL, before TEXT,
    PRIMARY KEY (changeset_id, kind, entity_id));
CREATE INDEX IF NOT EXISTS ix_touch_entity ON changeset_touch(kind, entity_id);

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, applied_seq INTEGER NOT NULL,
    created_at TEXT NOT NULL, data TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_calls (
    id TEXT PRIMARY KEY, run_id TEXT, step TEXT NOT NULL, model TEXT NOT NULL,
    cached INTEGER NOT NULL, cost_usd REAL NOT NULL, created_at TEXT NOT NULL,
    data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL);

-- v2: graph-building workspace (all additive)
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, ordinal INTEGER NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_chunk_source ON chunks(source_id, ordinal);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_tests (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_traces (
    id TEXT PRIMARY KEY, test_id TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, created_at TEXT NOT NULL,
    data TEXT NOT NULL);

-- v3: learning side (all additive). observations are insert-only (see store/learning.py).
CREATE TABLE IF NOT EXISTS learners (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learn_sessions (
    id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, learner_id TEXT NOT NULL,
    created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_turn_session ON turns(session_id, created_at);
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, concept_id TEXT NOT NULL,
    created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_obs_learner ON observations(learner_id, created_at);
CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, concept_id TEXT,
    created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_int_learner ON interactions(learner_id, created_at);
CREATE TABLE IF NOT EXISTS policy_decisions (
    id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learner_notes (learner_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learner_state_cache (
    learner_id TEXT NOT NULL, model_id TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (learner_id, model_id));
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, match_status TEXT NOT NULL, source_id TEXT, created_at TEXT NOT NULL,
    data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_targets (
    task_id TEXT NOT NULL, concept_id TEXT NOT NULL, weight REAL NOT NULL,
    PRIMARY KEY (task_id, concept_id));
CREATE INDEX IF NOT EXISTS ix_tt_concept ON task_targets(concept_id);
CREATE TABLE IF NOT EXISTS task_attempts (
    id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, task_id TEXT NOT NULL,
    created_at TEXT NOT NULL, data TEXT NOT NULL);
"""


class Database:
    """Thin wrapper owning one connection and a re-entrant transaction context."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        self._depth = 0

    def migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"database schema {version} is newer than code {SCHEMA_VERSION}")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic block. Nested use joins the outer transaction."""
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outermost:
                    self._conn.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    def close(self) -> None:
        self._conn.close()
