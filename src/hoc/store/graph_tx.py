"""GraphTx: the single write path to the materialised graph tables.

It enforces lock rules, bumps versions and records the *before* state of every element it
touches, which is what makes ChangeSets revertible without per-op inverse logic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Literal

from hoc.core.errors import LockedElementError
from hoc.core.knowledge import (
    Concept,
    ConceptStatus,
    Misconception,
    Relation,
    RelationStatus,
)

EntityKind = Literal["concept", "relation", "misconception"]
_TABLE: dict[EntityKind, str] = {
    "concept": "concepts",
    "relation": "relations",
    "misconception": "misconceptions",
}


class GraphTx:
    def __init__(self, conn: sqlite3.Connection, *, enforce_locks: bool, record: bool = True):
        self._conn = conn
        self._enforce_locks = enforce_locks
        self._record = record
        #: first-seen JSON (or None if absent) per touched element, in touch order
        self.before: dict[tuple[EntityKind, str], str | None] = {}

    # ---------------------------------------------------------------- reads

    def _raw(self, kind: EntityKind, id_: str) -> str | None:
        row = self._conn.execute(f"SELECT data FROM {_TABLE[kind]} WHERE id = ?", (id_,)).fetchone()
        return None if row is None else str(row["data"])

    def raw(self, kind: EntityKind, id_: str) -> str | None:
        """Current stored JSON of an element (None if absent)."""
        return self._raw(kind, id_)

    def get_concept(self, id_: str) -> Concept | None:
        raw = self._raw("concept", id_)
        return None if raw is None else Concept.model_validate_json(raw)

    def get_relation(self, id_: str) -> Relation | None:
        raw = self._raw("relation", id_)
        return None if raw is None else Relation.model_validate_json(raw)

    def get_misconception(self, id_: str) -> Misconception | None:
        raw = self._raw("misconception", id_)
        return None if raw is None else Misconception.model_validate_json(raw)

    def relations_of(self, concept_id: str) -> list[Relation]:
        """Active relations with the concept as source or target."""
        rows = self._conn.execute(
            "SELECT data FROM relations WHERE (source = ? OR target = ?) AND status != ?",
            (concept_id, concept_id, RelationStatus.REMOVED.value),
        ).fetchall()
        return [Relation.model_validate_json(r["data"]) for r in rows]

    def all_misconceptions(self) -> Iterator[Misconception]:
        for r in self._conn.execute("SELECT data FROM misconceptions").fetchall():
            yield Misconception.model_validate_json(r["data"])

    def active_relations(self, types: set[str]) -> list[Relation]:
        marks = ",".join("?" for _ in types)
        rows = self._conn.execute(
            f"SELECT data FROM relations WHERE status != ? AND type IN ({marks})",
            (RelationStatus.REMOVED.value, *sorted(types)),
        ).fetchall()
        return [Relation.model_validate_json(r["data"]) for r in rows]

    # --------------------------------------------------------------- writes

    def _note_before(self, kind: EntityKind, id_: str) -> str | None:
        raw = self._raw(kind, id_)
        if self._record and (kind, id_) not in self.before:
            self.before[(kind, id_)] = raw
        return raw

    def put_concept(self, concept: Concept) -> None:
        raw = self._note_before("concept", concept.id)
        if raw is not None:
            old = Concept.model_validate_json(raw)
            if old.locked and self._enforce_locks:
                raise LockedElementError(f"concept {old.id!r} is locked; change needs approval")
            concept = concept.model_copy(update={"version": old.version + 1})
        self._conn.execute(
            "INSERT OR REPLACE INTO concepts (id, status, data) VALUES (?, ?, ?)",
            (concept.id, concept.status.value, concept.model_dump_json()),
        )

    def put_relation(self, relation: Relation) -> None:
        raw = self._note_before("relation", relation.id)
        if raw is not None:
            old = Relation.model_validate_json(raw)
            if old.locked and self._enforce_locks:
                raise LockedElementError(f"relation {old.id!r} is locked; change needs approval")
            relation = relation.model_copy(update={"version": old.version + 1})
        self._conn.execute(
            "INSERT OR REPLACE INTO relations (id, type, source, target, status, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                relation.id,
                relation.type.value,
                relation.source,
                relation.target,
                relation.status.value,
                relation.model_dump_json(),
            ),
        )

    def put_misconception(self, misconception: Misconception) -> None:
        raw = self._note_before("misconception", misconception.id)
        if raw is not None:
            old = Misconception.model_validate_json(raw)
            misconception = misconception.model_copy(update={"version": old.version + 1})
        self._conn.execute(
            "INSERT OR REPLACE INTO misconceptions (id, data) VALUES (?, ?)",
            (misconception.id, misconception.model_dump_json()),
        )

    # ------------------------------------------------------ revert & promote

    def restore(self, kind: EntityKind, id_: str, raw: str | None) -> None:
        """Put an element back exactly as it was (or delete it if it did not exist)."""
        if raw is None:
            self._conn.execute(f"DELETE FROM {_TABLE[kind]} WHERE id = ?", (id_,))
            return
        if kind == "concept":
            c = Concept.model_validate_json(raw)
            self._conn.execute(
                "INSERT OR REPLACE INTO concepts (id, status, data) VALUES (?, ?, ?)",
                (c.id, c.status.value, raw),
            )
        elif kind == "relation":
            r = Relation.model_validate_json(raw)
            self._conn.execute(
                "INSERT OR REPLACE INTO relations (id, type, source, target, status, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r.id, r.type.value, r.source, r.target, r.status.value, raw),
            )
        else:
            m = Misconception.model_validate_json(raw)
            self._conn.execute(
                "INSERT OR REPLACE INTO misconceptions (id, data) VALUES (?, ?)", (m.id, raw)
            )

    def promote(self, kind: EntityKind, id_: str) -> None:
        """Expert confirmation: proposed -> accepted and locked. No version bump."""
        raw = self._raw(kind, id_)
        if raw is None:
            return
        if kind == "concept":
            c = Concept.model_validate_json(raw)
            if c.status is ConceptStatus.PROPOSED:
                c = c.model_copy(update={"status": ConceptStatus.ACCEPTED})
            if c.status.is_active:
                c = c.model_copy(update={"locked": True})
            self.restore("concept", id_, c.model_dump_json())
        elif kind == "relation":
            r = Relation.model_validate_json(raw)
            if r.status is RelationStatus.PROPOSED:
                r = r.model_copy(update={"status": RelationStatus.ACCEPTED})
            if r.status.is_active:
                r = r.model_copy(update={"locked": True})
            self.restore("relation", id_, r.model_dump_json())
