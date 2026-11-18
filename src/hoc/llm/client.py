"""LLMClient: prompt rendering, structured output, caching, retry, and call logging."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from hoc.config import LLMSettings
from hoc.core.common import utc_now
from hoc.core.errors import LLMError
from hoc.core.provenance import LLMCall
from hoc.llm.backend import BackendRequest, LLMBackend
from hoc.llm.prompts import PromptRegistry
from hoc.llm.schema import portable_schema
from hoc.store.db import Database

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    call_id: str  # reference this from any record the output produced
    cached: bool


class LLMClient:
    def __init__(
        self,
        backend: LLMBackend,
        db: Database,
        settings: LLMSettings,
        prompts: PromptRegistry,
    ) -> None:
        self._backend = backend
        self._db = db
        self._settings = settings
        self._prompts = prompts

    def structured(
        self,
        step: str,
        variables: dict[str, object],
        output_model: type[T],
        *,
        run_id: str | None = None,
        prompt_version: str | None = None,
    ) -> StructuredResult[T]:
        template = self._prompts.get(step, prompt_version)
        prompt = template.render(variables)
        model = self._settings.model_for(step)
        schema = portable_schema(output_model.model_json_schema())
        max_tokens = self._settings.max_tokens_for(step)
        key = _cache_key(model, prompt.system, prompt.user, schema, max_tokens)

        if self._settings.cache_enabled:
            hit = self._cache_get(key)
            if hit is not None:
                try:
                    value = output_model.model_validate(hit)
                except ValidationError:
                    pass  # stale cache entry from an older schema: fall through and recompute
                else:
                    call = self._log(step, model, prompt, key, hit, 0, 0, 0, True, 1, run_id)
                    return StructuredResult(value, call.id, True)

        user = prompt.user
        total_in = total_out = 0
        started = time.monotonic()
        last_error = "unknown"
        for attempt in range(1, self._settings.max_attempts + 1):
            response = self._backend.complete(
                BackendRequest(
                    model=model,
                    system=prompt.system,
                    user=user,
                    schema=schema,
                    schema_name=output_model.__name__,
                    max_tokens=max_tokens,
                )
            )
            total_in += response.input_tokens
            total_out += response.output_tokens
            try:
                value = output_model.model_validate(response.output)
            except ValidationError as e:
                last_error = str(e)
                user = (
                    f"{prompt.user}\n\nYour previous answer failed validation:\n{last_error}\n"
                    "Return a corrected answer."
                )
                continue
            latency = int((time.monotonic() - started) * 1000)
            if self._settings.cache_enabled:
                self._cache_put(key, response.output)
            call = self._log(
                step,
                model,
                prompt,
                key,
                response.output,
                total_in,
                total_out,
                latency,
                False,
                attempt,
                run_id,
            )
            return StructuredResult(value, call.id, False)

        self._log(
            step,
            model,
            prompt,
            key,
            None,
            total_in,
            total_out,
            int((time.monotonic() - started) * 1000),
            False,
            self._settings.max_attempts,
            run_id,
            error=last_error,
        )
        raise LLMError(
            f"step {step!r}: output failed validation after "
            f"{self._settings.max_attempts} attempts: {last_error}"
        )

    def get_call(self, call_id: str) -> LLMCall:
        """The logged record of a call (tokens, cost, latency, cache hit)."""
        with self._db.read() as conn:
            row = conn.execute("SELECT data FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        if row is None:
            raise LLMError(f"unknown LLM call {call_id!r}")
        return LLMCall.model_validate_json(row["data"])

    # -------------------------------------------------------------- internals

    def _cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        price = self._settings.pricing.get(model)
        if price is None:
            return 0.0
        return (tokens_in * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1_000_000

    def _log(
        self,
        step: str,
        model: str,
        prompt: Any,
        key: str,
        output: dict[str, Any] | None,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        cached: bool,
        attempts: int,
        run_id: str | None,
        error: str | None = None,
    ) -> LLMCall:
        call = LLMCall(
            run_id=run_id,
            step=step,
            model=model,
            prompt_version=prompt.version,
            input_hash=key,
            system=prompt.system,
            user=prompt.user,
            output=output,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=self._cost(model, tokens_in, tokens_out),
            latency_ms=latency_ms,
            cached=cached,
            attempts=attempts,
            error=error,
        )
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_calls "
                "(id, run_id, step, model, cached, cost_usd, created_at, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    call.id,
                    run_id,
                    step,
                    model,
                    int(cached),
                    call.cost_usd,
                    call.created_at.isoformat(),
                    call.model_dump_json(),
                ),
            )
        return call

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return None if row is None else dict(json.loads(row["response"]))

    def _cache_put(self, key: str, output: dict[str, Any]) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, response, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(output), utc_now().isoformat()),
            )


def _cache_key(model: str, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> str:
    payload = json.dumps(
        {"m": model, "s": system, "u": user, "schema": schema, "t": max_tokens}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def usage_summary(db: Database) -> list[dict[str, Any]]:
    """Per-step totals of calls, cache hits, tokens and cost (for ``hoc llm usage``)."""
    with db.read() as conn:
        rows = conn.execute("SELECT data FROM llm_calls").fetchall()
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = LLMCall.model_validate_json(r["data"])
        a = agg.setdefault(
            c.step,
            {"step": c.step, "calls": 0, "cache_hits": 0, "in": 0, "out": 0, "cost_usd": 0.0},
        )
        a["calls"] += 1
        a["cache_hits"] += int(c.cached)
        a["in"] += c.input_tokens
        a["out"] += c.output_tokens
        a["cost_usd"] += c.cost_usd
    return sorted(agg.values(), key=lambda a: a["step"])
