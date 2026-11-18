# ADR 0003 — Auto-apply vs. approve, and locks

**Decision.**
- `apply` (auto) is for agent output the triage rule trusts. Elements it adds are `proposed`.
- `approve` is an expert act: it applies a proposed ChangeSet (or confirms an auto-applied one);
  touched elements become `accepted` and `locked`.
- A locked element can only be changed by a ChangeSet that is *approved*; auto-apply raises
  `LockedElementError`. Removals are soft (status `removed`/`deprecated`/`merged`), never
  deletes, and concept ids are never reused.

**Why.** It encodes "agents propose, experts decide" in the write path rather than in
convention, so no future pipeline stage can bypass review by accident.
