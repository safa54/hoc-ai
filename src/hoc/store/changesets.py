"""ChangeSet lifecycle: propose -> (apply | approve | reject) -> revert.

All graph mutation in the system funnels through ``ChangeSetStore``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from hoc.core.build import GraphDiff
from hoc.core.changes import ChangeSet, ChangeSetStatus
from hoc.core.common import Actor, utc_now
from hoc.core.errors import (
    ChangeSetStateError,
    HocError,
    NotFoundError,
    RevertConflictError,
)
from hoc.store.apply import ApplyContext, apply_changeset_ops, apply_op, check_invariants
from hoc.store.db import Database
from hoc.store.graph_tx import EntityKind, GraphTx
from hoc.store.history import diff_from_befores

Mode = Literal["auto", "approved"]


@dataclass(frozen=True)
class Preview:
    """Result of dry-running a ChangeSet against the current graph (nothing is written)."""

    diff: GraphDiff
    errors: list[tuple[int, str]] = field(default_factory=list)  # (op index, message)
    touches_locked: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors


class _Abort(Exception):
    """Raised to roll back a dry run."""


class ChangeSetStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --------------------------------------------------------------- queries

    def get(self, changeset_id: str) -> ChangeSet:
        with self._db.read() as conn:
            return self._load(conn, changeset_id)

    def find(self, status: ChangeSetStatus | None = None, limit: int = 100) -> list[ChangeSet]:
        sql = "SELECT data FROM changesets"
        params: tuple[object, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self._db.read() as conn:
            rows = conn.execute(sql, (*params, limit)).fetchall()
        return [ChangeSet.model_validate_json(r["data"]) for r in rows]

    def touched(self, changeset_id: str) -> list[tuple[EntityKind, str]]:
        with self._db.read() as conn:
            return self._touched(conn, changeset_id)

    def applied(self) -> list[ChangeSet]:
        """Currently applied ChangeSets in application order (the graph's history)."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT data FROM changesets WHERE status IN ('auto_applied', 'approved') "
                "ORDER BY applied_seq"
            ).fetchall()
        return [ChangeSet.model_validate_json(r["data"]) for r in rows]

    def last_seq(self) -> int:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(applied_seq), 0) FROM changesets "
                "WHERE status IN ('auto_applied', 'approved')"
            ).fetchone()
        return int(row[0])

    def touching(self, kind: EntityKind, entity_id: str) -> list[ChangeSet]:
        """Applied ChangeSets that touched one element, oldest first."""
        with self._db.read() as conn:
            rows = conn.execute(
                """SELECT c.data FROM changesets c JOIN changeset_touch t
                   ON t.changeset_id = c.id WHERE t.kind = ? AND t.entity_id = ?
                   AND c.status IN ('auto_applied', 'approved') ORDER BY c.applied_seq""",
                (kind, entity_id),
            ).fetchall()
        return [ChangeSet.model_validate_json(r["data"]) for r in rows]

    def diff(self, changeset_id: str) -> GraphDiff:
        """What an applied ChangeSet changed. Exact only until a later one touches the same
        elements, so callers record it right after applying."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT kind, entity_id, before FROM changeset_touch WHERE changeset_id = ? "
                "ORDER BY ordinal",
                (changeset_id,),
            ).fetchall()
            tx = GraphTx(conn, enforce_locks=False, record=False)
            return diff_from_befores(
                [(r["kind"], r["entity_id"], r["before"]) for r in rows], tx.raw
            )

    def preview(self, cs: ChangeSet) -> Preview:
        """Dry-run each op in order. Failing ops are skipped and reported, so the caller can
        drop them and still use the rest."""
        result: list[Preview] = []
        try:
            with self._db.transaction() as conn:
                tx = GraphTx(conn, enforce_locks=False)
                ctx = ApplyContext(author=cs.author)
                errors: list[tuple[int, str]] = []
                for index, op in enumerate(cs.ops):
                    known = set(tx.before)
                    conn.execute("SAVEPOINT op")
                    try:
                        apply_op(tx, op, ctx)
                        check_invariants(tx)
                    except HocError as e:
                        conn.execute("ROLLBACK TO op")
                        errors.append((index, str(e)))
                        for key in set(tx.before) - known:
                            del tx.before[key]
                    finally:
                        conn.execute("RELEASE op")
                locked = any(
                    raw is not None and json.loads(raw).get("locked") for raw in tx.before.values()
                )
                diff = diff_from_befores([(k, i, b) for (k, i), b in tx.before.items()], tx.raw)
                result.append(Preview(diff=diff, errors=errors, touches_locked=locked))
                raise _Abort
        except _Abort:
            pass
        return result[0]

    # ------------------------------------------------------------ transitions

    def propose(self, cs: ChangeSet) -> ChangeSet:
        """Store a ChangeSet without touching the graph."""
        cs = cs.model_copy(
            update={"status": ChangeSetStatus.PROPOSED, "applied_seq": None, "applied_at": None}
        )
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM changesets WHERE id = ?", (cs.id,)).fetchone():
                raise ChangeSetStateError(f"changeset {cs.id!r} already exists")
            self._save(conn, cs)
        return cs

    def apply(self, changeset_id: str, decided_by: Actor | None = None) -> ChangeSet:
        """Apply a proposed ChangeSet without confirming it: elements stay ``proposed`` and
        unlocked, so agents can keep refining them. Refuses to modify locked elements.

        ``decided_by`` records a human who let it through without locking.
        """
        return self._apply(changeset_id, mode="auto", decided_by=decided_by)

    def approve(self, changeset_id: str, decided_by: Actor) -> ChangeSet:
        """Expert approval. Applies it if still proposed; confirms it if already auto-applied.

        Elements it touched become ``accepted`` and ``locked``.
        """
        with self._db.transaction() as conn:
            cs = self._load(conn, changeset_id)
            if cs.status is ChangeSetStatus.PROPOSED:
                return self._apply(changeset_id, mode="approved", decided_by=decided_by)
            if cs.status is ChangeSetStatus.AUTO_APPLIED:
                tx = GraphTx(conn, enforce_locks=False, record=False)
                for kind, id_ in self._touched(conn, changeset_id):
                    tx.promote(kind, id_)
                cs = cs.model_copy(
                    update={"status": ChangeSetStatus.APPROVED, "decided_by": decided_by}
                )
                self._save(conn, cs)
                return cs
            raise ChangeSetStateError(f"cannot approve a {cs.status.value} changeset")

    def reject(self, changeset_id: str, decided_by: Actor) -> ChangeSet:
        with self._db.transaction() as conn:
            cs = self._load(conn, changeset_id)
            if cs.status is not ChangeSetStatus.PROPOSED:
                raise ChangeSetStateError(f"cannot reject a {cs.status.value} changeset")
            cs = cs.model_copy(
                update={"status": ChangeSetStatus.REJECTED, "decided_by": decided_by}
            )
            self._save(conn, cs)
            return cs

    def revert(self, changeset_id: str) -> ChangeSet:
        """Undo an applied ChangeSet by restoring the before-state of everything it touched.

        Refused if a *later* applied ChangeSet touched the same elements.
        """
        with self._db.transaction() as conn:
            cs = self._load(conn, changeset_id)
            if not cs.status.is_applied or cs.applied_seq is None:
                raise ChangeSetStateError(f"cannot revert a {cs.status.value} changeset")
            conflicts = conn.execute(
                """SELECT DISTINCT c.id FROM changeset_touch t
                   JOIN changesets c ON c.id = t.changeset_id
                   WHERE c.applied_seq > ? AND c.status IN ('auto_applied', 'approved')
                     AND (t.kind, t.entity_id) IN
                         (SELECT kind, entity_id FROM changeset_touch WHERE changeset_id = ?)""",
                (cs.applied_seq, changeset_id),
            ).fetchall()
            if conflicts:
                ids = ", ".join(r["id"] for r in conflicts)
                raise RevertConflictError(f"later changesets touched the same elements: {ids}")
            tx = GraphTx(conn, enforce_locks=False, record=False)
            rows = conn.execute(
                "SELECT kind, entity_id, before FROM changeset_touch WHERE changeset_id = ? "
                "ORDER BY ordinal DESC",
                (changeset_id,),
            ).fetchall()
            for row in rows:
                tx.restore(row["kind"], row["entity_id"], row["before"])
            cs = cs.model_copy(update={"status": ChangeSetStatus.REVERTED})
            self._save(conn, cs)
            return cs

    # --------------------------------------------------------------- internals

    def _apply(self, changeset_id: str, *, mode: Mode, decided_by: Actor | None) -> ChangeSet:
        with self._db.transaction() as conn:
            cs = self._load(conn, changeset_id)
            if cs.status is not ChangeSetStatus.PROPOSED:
                raise ChangeSetStateError(f"cannot apply a {cs.status.value} changeset")
            approved = mode == "approved"
            tx = GraphTx(conn, enforce_locks=not approved)
            apply_changeset_ops(tx, cs)
            check_invariants(tx)
            if approved:
                for kind, id_ in list(tx.before):
                    tx.promote(kind, id_)
            seq = conn.execute(
                "SELECT COALESCE(MAX(applied_seq), 0) + 1 FROM changesets"
            ).fetchone()[0]
            for ordinal, ((kind, id_), before) in enumerate(tx.before.items()):
                conn.execute(
                    "INSERT INTO changeset_touch (changeset_id, ordinal, kind, entity_id, before) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (cs.id, ordinal, kind, id_, before),
                )
            cs = cs.model_copy(
                update={
                    "status": ChangeSetStatus.APPROVED
                    if approved
                    else ChangeSetStatus.AUTO_APPLIED,
                    "applied_seq": seq,
                    "applied_at": utc_now(),
                    "decided_by": decided_by,
                }
            )
            self._save(conn, cs)
            return cs

    @staticmethod
    def _load(conn: sqlite3.Connection, changeset_id: str) -> ChangeSet:
        row = conn.execute("SELECT data FROM changesets WHERE id = ?", (changeset_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"changeset {changeset_id!r} not found")
        return ChangeSet.model_validate_json(row["data"])

    @staticmethod
    def _touched(conn: sqlite3.Connection, changeset_id: str) -> list[tuple[EntityKind, str]]:
        rows = conn.execute(
            "SELECT kind, entity_id FROM changeset_touch WHERE changeset_id = ? ORDER BY ordinal",
            (changeset_id,),
        ).fetchall()
        return [(r["kind"], r["entity_id"]) for r in rows]

    @staticmethod
    def _save(conn: sqlite3.Connection, cs: ChangeSet) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO changesets (id, status, applied_seq, created_at, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                cs.id,
                cs.status.value,
                cs.applied_seq,
                cs.created_at.isoformat(),
                cs.model_dump_json(),
            ),
        )
