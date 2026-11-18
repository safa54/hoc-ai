"""Runner: drives a BuildEngine in a background thread with pause / resume / stop / step.

The runner is the only place that knows about threads. It never decides *what* to do (the
coordinator does) and never touches the graph (the engine does); it only decides *when*:
it waits while paused, while the expert is needed, and while there is nothing to do.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from hoc.construct.engine import BuildEngine
from hoc.core.build import RunMode, RunState, RunStatus
from hoc.core.common import utc_now
from hoc.store.workspace import EventLog, MetaStore

log = logging.getLogger(__name__)
RUN_STATE = "run_state"
_IDLE_RECHECK = 5.0


class Runner:
    def __init__(self, engine: BuildEngine, meta: MetaStore, events: EventLog) -> None:
        self._engine = engine
        self._meta = meta
        self._events = events
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._paused = False
        self._thread: threading.Thread | None = None
        self._stepping = False
        self._lock = threading.Lock()
        saved = meta.get(RUN_STATE, RunState) or RunState()
        # a thread never survives a restart: come back idle, keep the chosen mode
        self._state = RunState(mode=saved.mode, detail="Not started")
        self._meta.put(RUN_STATE, self._state)

    # ---------------------------------------------------------------- queries

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --------------------------------------------------------------- controls

    def start(self, mode: RunMode | None = None) -> RunState:
        with self._lock:
            if mode is not None:
                self._set(mode=mode)
            if self.alive:
                self._paused = False
                self._wake.set()
                return self._state
            self._stop.clear()
            self._paused = False
            self._engine.start_run(self._state.mode)
            self._thread = threading.Thread(target=self._loop, name="hoc-runner", daemon=True)
            self._set(status=RunStatus.RUNNING, detail="Starting", error=None)
            self._thread.start()
            return self._state

    def pause(self) -> RunState:
        self._paused = True
        self._wake.set()
        if not self.alive:
            self._set(status=RunStatus.PAUSED, detail="Paused")
        return self._state

    def resume(self) -> RunState:
        if not self.alive:
            return self.start()
        self._paused = False
        self._wake.set()
        return self._state

    def stop(self, timeout: float | None = None) -> RunState:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and timeout is not None:
            thread.join(timeout)
        if not self.alive:
            self._set(status=RunStatus.IDLE, detail="Stopped", current_agent=None)
        return self._state

    def set_mode(self, mode: RunMode) -> RunState:
        self._set(mode=mode)
        self._wake.set()
        return self._state

    def wake(self) -> None:
        """New input arrived (feedback, sources, decisions): re-check immediately."""
        self._wake.set()

    def step_once(self) -> RunState:
        """Run exactly one step in the background, whatever the run status."""
        with self._lock:
            if self._stepping or (self.alive and not self._paused):
                return self._state
            self._stepping = True
        threading.Thread(target=self._single_step, name="hoc-step", daemon=True).start()
        return self._state

    # --------------------------------------------------------------- internals

    def _single_step(self) -> None:
        previous = self._state.status
        try:
            self._run_one()
            if self._state.status is not RunStatus.ERROR:
                status = previous if previous is not RunStatus.RUNNING else RunStatus.PAUSED
                self._set(status=status, current_agent=None)
        finally:
            self._stepping = False

    def _run_one(self) -> str:
        """One engine step with status reporting. Returns the outcome kind."""
        task = self._engine.next_task()
        self._set(
            status=RunStatus.RUNNING,
            detail=task.label if task else "Checking the graph",
            current_agent=task.role if task else None,
            error=None,
        )
        try:
            outcome = self._engine.step(self._state.mode)
        except Exception as e:  # any failure pauses the run; the expert decides what next
            log.exception("build step failed")
            self._paused = True
            self._set(
                status=RunStatus.ERROR,
                detail="Step failed — fix and resume",
                error=str(e),
                current_agent=None,
            )
            return "error"
        if outcome.kind == "idle":
            self._set(status=RunStatus.IDLE, detail=outcome.summary, current_agent=None)
        else:
            self._set(detail=outcome.summary, current_agent=None)
        return outcome.kind

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._paused:
                if self._state.status is not RunStatus.ERROR:
                    self._set(status=RunStatus.PAUSED, detail="Paused", current_agent=None)
                self._wait()
                continue
            reason = self._engine.blocked(self._state.mode)
            if reason:
                self._set(status=RunStatus.WAITING, detail=reason, current_agent=None)
                self._wait(_IDLE_RECHECK)
                continue
            if self._run_one() == "idle":
                self._wait(_IDLE_RECHECK)
        self._set(status=RunStatus.IDLE, detail="Stopped", current_agent=None)

    def _wait(self, timeout: float | None = None) -> None:
        self._wake.wait(timeout)
        self._wake.clear()

    def _set(self, **changes: Any) -> None:
        new = self._state.model_copy(update=changes)
        visible = ("status", "mode", "detail", "current_agent", "error")
        if all(getattr(new, k) == getattr(self._state, k) for k in visible):
            return
        self._state = new.model_copy(update={"updated_at": utc_now()})
        self._meta.put(RUN_STATE, self._state)
        self._events.append("run.status", self._state.model_dump(mode="json"))
