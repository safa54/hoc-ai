"""Budget guard: a learning session must never turn into an unbounded spend.

Counts real (non-cached) calls of the learning-side steps from the LLM call log, so the tutor and
the open-answer grader share one budget and nothing can slip past it.
"""

from __future__ import annotations

from dataclasses import dataclass

from hoc.config import BudgetSettings
from hoc.store.learning import CallCounter, SessionStore, day_start_iso

LEARN_STEPS = ("tutor_turn", "tutor_grade")


@dataclass
class BudgetGuard:
    settings: BudgetSettings
    calls: CallCounter
    sessions: SessionStore

    def usage(self, session_id: str) -> dict[str, int]:
        started = self.sessions.get(session_id).started_at.isoformat() if session_id else None
        return {
            "session_calls": self.calls.count(LEARN_STEPS, started),
            "day_calls": self.calls.count(LEARN_STEPS, day_start_iso()),
            "session_limit": self.settings.max_llm_calls_per_session,
            "day_limit": self.settings.max_llm_calls_per_day,
        }

    def exceeded(self, session_id: str) -> str | None:
        u = self.usage(session_id)
        if u["session_calls"] >= u["session_limit"]:
            return (
                f"This session has used its budget of {u['session_limit']} model calls. "
                "What next?, exercises with exact answers, flags and the map still work; "
                "raise `learn.budget.max_llm_calls_per_session` in the config to chat more."
            )
        if u["day_calls"] >= u["day_limit"]:
            return (
                f"Today's budget of {u['day_limit']} model calls is used up. "
                "Raise `learn.budget.max_llm_calls_per_day` to continue."
            )
        return None
