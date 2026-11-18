"""Learning-side stores (LEARNING_SIDE_PLAN L3.3). One topic file holds the graph and everyone
who learns from it, because observations reference that graph's concept ids.

``ObservationStore`` has **no update or delete method on purpose** (invariant: evidence is
immutable; beliefs are derived and disposable).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from hoc.core.common import utc_now
from hoc.core.errors import NotFoundError
from hoc.core.learner import Learner, Observation, PolicyDecision, Session, Turn
from hoc.core.learning import (
    Interaction,
    LearnerNotes,
    LearnerState,
    NoteEntry,
    PendingCheck,
    TaskAttempt,
)
from hoc.store.db import Database

DEFAULT_LEARNER = "me"


class LearnerStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ensure_default(self) -> Learner:
        try:
            return self.get(DEFAULT_LEARNER)
        except NotFoundError:
            return self.save(Learner(id=DEFAULT_LEARNER, display_name="Me"))

    def save(self, learner: Learner) -> Learner:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO learners (id, data) VALUES (?, ?)",
                (learner.id, learner.model_dump_json()),
            )
        return learner

    def get(self, learner_id: str) -> Learner:
        with self._db.read() as conn:
            row = conn.execute("SELECT data FROM learners WHERE id = ?", (learner_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"learner {learner_id!r} not found")
        return Learner.model_validate_json(row["data"])

    def all(self) -> list[Learner]:
        with self._db.read() as conn:
            rows = conn.execute("SELECT data FROM learners ORDER BY rowid").fetchall()
        return [Learner.model_validate_json(r["data"]) for r in rows]


class SessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def start(self, learner_id: str) -> Session:
        session = Session(learner_id=learner_id)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO learn_sessions (id, learner_id, created_at, data) VALUES (?,?,?,?)",
                (session.id, learner_id, session.started_at.isoformat(), session.model_dump_json()),
            )
        return session

    def get(self, session_id: str) -> Session:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT data FROM learn_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"session {session_id!r} not found")
        return Session.model_validate_json(row["data"])

    def latest(self, learner_id: str) -> Session | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT data FROM learn_sessions WHERE learner_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (learner_id,),
            ).fetchone()
        return None if row is None else Session.model_validate_json(row["data"])


class TurnStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, turn: Turn) -> Turn:
        if turn.session_id is None or turn.learner_id is None:
            raise ValueError("a turn needs a session and a learner")
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO turns (id, session_id, learner_id, created_at, data) "
                "VALUES (?,?,?,?,?)",
                (
                    turn.id,
                    turn.session_id,
                    turn.learner_id,
                    turn.timestamp.isoformat(),
                    turn.model_dump_json(),
                ),
            )
        return turn

    def get(self, turn_id: str) -> Turn:
        with self._db.read() as conn:
            row = conn.execute("SELECT data FROM turns WHERE id = ?", (turn_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"turn {turn_id!r} not found")
        return Turn.model_validate_json(row["data"])

    def for_session(self, session_id: str, limit: int | None = None) -> list[Turn]:
        sql = "SELECT data FROM turns WHERE session_id = ? ORDER BY created_at, rowid"
        with self._db.read() as conn:
            rows = conn.execute(sql, (session_id,)).fetchall()
        turns = [Turn.model_validate_json(r["data"]) for r in rows]
        return turns[-limit:] if limit else turns

    def for_learner(self, learner_id: str) -> list[Turn]:
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT data FROM turns WHERE learner_id = ? ORDER BY created_at, rowid",
                (learner_id,),
            ).fetchall()
        return [Turn.model_validate_json(r["data"]) for r in rows]

    def count_tutor_calls(self, *, session_id: str | None = None, since: str | None = None) -> int:
        """Tutor turns that cost a real (non-cached) LLM call. Used by the budget guard."""
        sql = "SELECT data FROM turns WHERE 1=1"
        params: list[Any] = []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        with self._db.read() as conn:
            rows = conn.execute(sql, params).fetchall()
        n = 0
        for r in rows:
            cost = json.loads(r["data"]).get("meta", {}).get("cost", {})
            n += int(cost.get("calls", 0))
        return n


class ObservationStore:
    """Append-only evidence. There is intentionally no update() or delete()."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, obs: Observation) -> Observation:
        self.add_many([obs])
        return obs

    def add_many(self, observations: list[Observation]) -> None:
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT INTO observations (id, learner_id, concept_id, created_at, data) "
                "VALUES (?,?,?,?,?)",
                [
                    (
                        o.id,
                        o.learner_id,
                        o.target.id,
                        o.timestamp.isoformat(),
                        o.model_dump_json(),
                    )
                    for o in observations
                ],
            )

    def for_learner(self, learner_id: str, concept_id: str | None = None) -> list[Observation]:
        sql = "SELECT data FROM observations WHERE learner_id = ?"
        params: list[Any] = [learner_id]
        if concept_id is not None:
            sql += " AND concept_id = ?"
            params.append(concept_id)
        with self._db.read() as conn:
            rows = conn.execute(sql + " ORDER BY created_at, rowid", params).fetchall()
        return [Observation.model_validate_json(r["data"]) for r in rows]

    def count(self, learner_id: str) -> int:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM observations WHERE learner_id = ?", (learner_id,)
            ).fetchone()
        return int(row[0])


class InteractionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, item: Interaction) -> Interaction:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO interactions (id, learner_id, concept_id, created_at, data) "
                "VALUES (?,?,?,?,?)",
                (
                    item.id,
                    item.learner_id,
                    item.concept_id,
                    item.at.isoformat(),
                    item.model_dump_json(),
                ),
            )
        return item

    def for_learner(self, learner_id: str) -> list[Interaction]:
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT data FROM interactions WHERE learner_id = ? ORDER BY created_at, rowid",
                (learner_id,),
            ).fetchall()
        return [Interaction.model_validate_json(r["data"]) for r in rows]

    def count(self, learner_id: str) -> int:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE learner_id = ?", (learner_id,)
            ).fetchone()
        return int(row[0])


class DecisionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, decision: PolicyDecision) -> PolicyDecision:
        if decision.learner_id is None:
            raise ValueError("a decision needs a learner")
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO policy_decisions (id, learner_id, created_at, data) VALUES (?,?,?,?)",
                (
                    decision.id,
                    decision.learner_id,
                    decision.timestamp.isoformat(),
                    decision.model_dump_json(),
                ),
            )
        return decision

    def get(self, decision_id: str) -> PolicyDecision:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT data FROM policy_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"decision {decision_id!r} not found")
        return PolicyDecision.model_validate_json(row["data"])

    def set_accepted(self, decision_id: str, accepted: bool, chosen_index: int) -> None:
        d = self.get(decision_id).model_copy(
            update={"accepted": accepted, "chosen_index": chosen_index, "chosen_by": "learner"}
        )
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE policy_decisions SET data = ? WHERE id = ?", (d.model_dump_json(), d.id)
            )

    def for_learner(self, learner_id: str, limit: int | None = None) -> list[PolicyDecision]:
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT data FROM policy_decisions WHERE learner_id = ? ORDER BY created_at, rowid",
                (learner_id,),
            ).fetchall()
        items = [PolicyDecision.model_validate_json(r["data"]) for r in rows]
        return items[-limit:] if limit else items


class NotesStore:
    """Per-learner narrative notes and the open check question."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, learner_id: str) -> LearnerNotes:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT data FROM learner_notes WHERE learner_id = ?", (learner_id,)
            ).fetchone()
        return (
            LearnerNotes(learner_id=learner_id)
            if row is None
            else LearnerNotes.model_validate_json(row["data"])
        )

    def append(self, learner_id: str, text: str, turn_id: str | None) -> None:
        notes = self.get(learner_id)
        notes.entries.append(NoteEntry(text=text, turn_id=turn_id))
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO learner_notes (learner_id, data) VALUES (?, ?)",
                (learner_id, notes.model_dump_json()),
            )

    def pending_check(self, learner_id: str) -> PendingCheck | None:
        raw = _meta_get(self._db, f"pending_check:{learner_id}")
        if raw is None:
            return None
        check = PendingCheck.model_validate_json(raw)
        return None if check.resolved else check

    def set_pending_check(self, learner_id: str, check: PendingCheck | None) -> None:
        key = f"pending_check:{learner_id}"
        if check is None:
            with self._db.transaction() as conn:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            _meta_put(self._db, key, check)


def _meta_get(db: Database, key: str) -> str | None:
    with db.read() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _meta_put(db: Database, key: str, value: BaseModel) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value.model_dump_json())
        )


class StateCache:
    """Derived state, keyed by (learner, model). Safe to wipe at any time."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, learner_id: str, model_id: str) -> LearnerState | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT data FROM learner_state_cache WHERE learner_id = ? AND model_id = ?",
                (learner_id, model_id),
            ).fetchone()
        return None if row is None else LearnerState.model_validate_json(row["data"])

    def put(self, state: LearnerState) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO learner_state_cache (learner_id, model_id, data) "
                "VALUES (?,?,?)",
                (state.learner_id, state.model_id, state.model_dump_json()),
            )

    def clear(self) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM learner_state_cache")


class AttemptStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, attempt: TaskAttempt) -> TaskAttempt:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_attempts (id, learner_id, task_id, created_at, data) "
                "VALUES (?,?,?,?,?)",
                (
                    attempt.id,
                    attempt.learner_id,
                    attempt.task_id,
                    attempt.created_at.isoformat(),
                    attempt.model_dump_json(),
                ),
            )
        return attempt

    def get(self, attempt_id: str) -> TaskAttempt:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT data FROM task_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"attempt {attempt_id!r} not found")
        return TaskAttempt.model_validate_json(row["data"])

    def for_learner(self, learner_id: str, task_id: str | None = None) -> list[TaskAttempt]:
        sql = "SELECT data FROM task_attempts WHERE learner_id = ?"
        params: list[Any] = [learner_id]
        if task_id:
            sql += " AND task_id = ?"
            params.append(task_id)
        with self._db.read() as conn:
            rows = conn.execute(sql + " ORDER BY created_at, rowid", params).fetchall()
        return [TaskAttempt.model_validate_json(r["data"]) for r in rows]


class CallCounter:
    """Real (non-cached) LLM calls from the call log: the single source of truth for budgets."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def count(self, steps: tuple[str, ...], since: str | None = None) -> int:
        marks = ",".join("?" for _ in steps)
        sql = f"SELECT COUNT(*) FROM llm_calls WHERE cached = 0 AND step IN ({marks})"
        params: list[Any] = list(steps)
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        with self._db.read() as conn:
            return int(conn.execute(sql, params).fetchone()[0])


def day_start_iso() -> str:
    now = utc_now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
