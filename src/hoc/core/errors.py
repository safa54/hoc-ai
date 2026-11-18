"""Domain errors. All inherit from HocError so callers (CLI/UI) can catch one type."""

from __future__ import annotations


class HocError(Exception):
    """Base class for expected, user-reportable errors."""


class NotFoundError(HocError):
    pass


class GraphConflictError(HocError):
    """An operation would violate a graph invariant (duplicate id, cycle, bad endpoint...)."""


class LockedElementError(GraphConflictError):
    """A non-approved change tried to modify an expert-locked element."""


class ChangeSetStateError(HocError):
    """A ChangeSet transition is not allowed from its current status."""


class RevertConflictError(ChangeSetStateError):
    """Reverting would clobber a later change to the same element."""


class LLMError(HocError):
    pass


class PromptError(HocError):
    pass
