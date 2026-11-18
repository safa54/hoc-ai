# ADR 0011 — Super-nodes are a view over areas, not graph semantics

**Decision.** Concepts already carry their area in `topic_ids` (set by the builders; topics are
navigation-only, ARCHITECTURE §4.1). `groupView` (frontend, pure) replaces each collapsed area by
one ordinary node and aggregates edges between collapsed groups and nodes (counted, deduplicated).
Because a super-node is just a node, dagre layout needs no compound support. Both sides use it
(Studio default: all expanded; Learn default: collapsed above `learn.collapse_threshold`
concepts, with a text progress bar and a status-coloured border). A concept can be moved to
another area from the Studio inspector (a navigation-only edit that does not lock it).

**Deferred.** Graph-level area operations (rename/split areas, an agent that regroups); flashing a
collapsed super-node when a change lands inside it; true compound layout (elkjs) if aggregated
layouts prove poor.
