"""Test doubles for the LLM layer. Lives in the package so downstream tests can reuse it."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hoc.core.errors import LLMError
from hoc.llm.backend import BackendRequest, BackendResponse

Responder = Callable[[BackendRequest], dict[str, Any]]


class ScriptedBackend:
    """Returns queued outputs (or calls a responder) and records every request it receives."""

    def __init__(
        self, outputs: list[dict[str, Any]] | None = None, responder: Responder | None = None
    ):
        self._outputs = list(outputs or [])
        self._responder = responder
        self.requests: list[BackendRequest] = []

    def complete(self, request: BackendRequest) -> BackendResponse:
        self.requests.append(request)
        if self._responder is not None:
            output = self._responder(request)
        elif self._outputs:
            output = self._outputs.pop(0)
        else:
            raise AssertionError("ScriptedBackend has no more outputs")
        return BackendResponse(output=output, input_tokens=100, output_tokens=20)


class FixtureBackend:
    """Replays recorded outputs from a JSON file: ``{schema_name: [output, output, ...]}``.

    Each schema's outputs are served in order; the last one repeats. Used by recorded-fixture
    tests and as the ``fixture`` provider kind for trying the UI without spending tokens.
    """

    def __init__(self, path: str | Path, delay: float | None = None) -> None:
        data = json.loads(Path(path).read_text())
        file_delay = float(data.pop("_delay", 0.0))
        self._delay = file_delay if delay is None else delay  # config beats the file
        self._outputs: dict[str, list[dict[str, Any]]] = data
        self._cursor: dict[str, int] = {}
        self._lock = threading.Lock()
        self.requests: list[BackendRequest] = []

    def complete(self, request: BackendRequest) -> BackendResponse:
        if self._delay:
            import time

            time.sleep(self._delay)
        with self._lock:
            self.requests.append(request)
            outputs = self._outputs.get(request.schema_name)
            if not outputs:
                raise LLMError(f"fixture has no output for {request.schema_name!r}")
            index = self._cursor.get(request.schema_name, 0)
            self._cursor[request.schema_name] = index + 1
            output = outputs[min(index, len(outputs) - 1)]
        return BackendResponse(output=output, input_tokens=100, output_tokens=50)
