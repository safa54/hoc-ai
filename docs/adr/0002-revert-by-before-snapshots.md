# ADR 0002 — ChangeSet revert via recorded before-states

**Context.** Every graph change must be traceable and reversible, across 12 op types.

**Decision.** `GraphTx` records the first-seen JSON of every element an apply touches
(`changeset_touch`). Revert restores those states in reverse order, instead of each op
defining an inverse. Revert is refused if a *later still-applied* ChangeSet touched any of the
same elements (`RevertConflictError`); reverting later ones first unblocks it.

**Consequences.** + One generic mechanism; new ops get revert for free; exact restoration
(including versions). − Reverts are all-or-nothing per ChangeSet and strictly LIFO per
element. Expert "approve" of an already-applied ChangeSet promotes elements in place without
disturbing the recorded before-state.
