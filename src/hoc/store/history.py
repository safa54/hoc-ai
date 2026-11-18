"""Graph history: element-level diffs of ChangeSets and the graph as it was at any step.

Both are derived from the before-states that ``GraphTx`` records (ADR 0002), so nothing
extra has to be written when a ChangeSet is applied.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from hoc.core.build import ElementChange, GraphDiff
from hoc.core.knowledge import Concept, Misconception, Relation
from hoc.store.db import Database
from hoc.store.graph_tx import EntityKind
from hoc.store.knowledge_graph import KnowledgeGraph

_TABLES: dict[EntityKind, str] = {
    "concept": "concepts",
    "relation": "relations",
    "misconception": "misconceptions",
}
_IGNORED = {"version", "created_at", "created_by"}


def _active(kind: str, doc: dict[str, Any] | None) -> bool:
    if doc is None:
        return False
    if kind == "concept":
        return doc["status"] in ("proposed", "accepted")
    if kind == "relation":
        return bool(doc["status"] != "removed")
    return True


def _label(kind: str, doc: dict[str, Any]) -> str:
    if kind == "relation":
        return f"{doc['source']} → {doc['target']} ({doc['type']})"
    return str(doc.get("name") or doc.get("id"))


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in after:
        if key in _IGNORED or before.get(key) == after.get(key):
            continue
        if key == "card" and isinstance(after[key], dict):
            old = before.get("card") or {}
            fields += [f"card.{k}" for k, v in after[key].items() if old.get(k) != v]
        else:
            fields.append(key)
    return fields


def element_change(
    kind: EntityKind, id_: str, before: str | None, after: str | None
) -> ElementChange | None:
    b = json.loads(before) if before else None
    a = json.loads(after) if after else None
    was, now = _active(kind, b), _active(kind, a)
    if not was and not now:
        return None
    if not was and a is not None:
        return ElementChange(kind=kind, id=id_, change="added", label=_label(kind, a))
    if was and not now and b is not None:
        detail = ""
        if kind == "concept" and a is not None:
            if a.get("redirect_to"):
                detail = f"merged into {a['redirect_to']}"
            elif a.get("split_into"):
                detail = "split into " + ", ".join(a["split_into"])
            else:
                detail = "deprecated"
        return ElementChange(
            kind=kind, id=id_, change="removed", label=_label(kind, b), detail=detail
        )
    assert a is not None and b is not None
    fields = _changed_fields(b, a)
    if not fields:
        return None
    return ElementChange(
        kind=kind, id=id_, change="updated", label=_label(kind, a), detail=", ".join(fields)
    )


def diff_from_befores(
    befores: Iterable[tuple[EntityKind, str, str | None]],
    current: Callable[[EntityKind, str], str | None],
) -> GraphDiff:
    changes = []
    for kind, id_, before in befores:
        change = element_change(kind, id_, before, current(kind, id_))
        if change is not None:
            changes.append(change)
    return GraphDiff(changes=changes)


def graph_at(db: Database, applied_seq: int) -> KnowledgeGraph:
    """The graph as it was right after the ChangeSet with ``applied_seq`` (0 = empty start).

    Starts from the current tables and undoes later ChangeSets newest-first.
    """
    with db.read() as conn:
        state: dict[str, dict[str, str]] = {
            kind: {r["id"]: r["data"] for r in conn.execute(f"SELECT id, data FROM {table}")}
            for kind, table in _TABLES.items()
        }
        rows = conn.execute(
            """SELECT t.kind, t.entity_id, t.before FROM changeset_touch t
               JOIN changesets c ON c.id = t.changeset_id
               WHERE c.applied_seq > ? AND c.status IN ('auto_applied', 'approved')
               ORDER BY c.applied_seq DESC, t.ordinal DESC""",
            (applied_seq,),
        ).fetchall()
    for r in rows:
        if r["before"] is None:
            state[r["kind"]].pop(r["entity_id"], None)
        else:
            state[r["kind"]][r["entity_id"]] = r["before"]
    return KnowledgeGraph(
        [Concept.model_validate_json(v) for v in state["concept"].values()],
        [Relation.model_validate_json(v) for v in state["relation"].values()],
        [Misconception.model_validate_json(v) for v in state["misconception"].values()],
    )
