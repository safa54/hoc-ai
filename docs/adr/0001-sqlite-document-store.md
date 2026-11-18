# ADR 0001 — SQLite with JSON documents instead of an ORM

**Status:** accepted (changes ARCHITECTURE §12.1, which proposed SQLAlchemy/SQLModel)

**Context.** Domain types are Pydantic models that evolve every milestone. An ORM would add a
second schema to keep in sync, and graph/learner data is read whole, not queried relationally.

**Decision.** Use stdlib `sqlite3`. Each entity is stored as validated JSON in a `data` column,
with only the columns needed for lookup/filtering (ids, status, endpoints, timestamps)
duplicated alongside. Schema is versioned with `PRAGMA user_version`.

**Consequences.** + Fewer dependencies, one source of truth for shapes (Pydantic), trivial
snapshots/copies. − No cross-document constraints in SQL (enforced in `store/apply.py`
instead); queries beyond the indexed columns load documents into Python. Acceptable at
prototype scale (thousands of nodes). Swap path: the `Database`/`GraphTx`/`ChangeSetStore`
classes are the only code that knows about SQL.
