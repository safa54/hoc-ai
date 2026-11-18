"""LLM backends. ``LLMBackend`` is the only provider-specific seam in the system.

Three implementations cover practically every model:
  * ``AnthropicBackend``   - Claude through the official SDK;
  * ``OpenAICompatBackend`` - anything that speaks the OpenAI chat API (OpenAI, Azure,
    OpenRouter, Gemini's compatibility endpoint, Ollama, vLLM, LM Studio, ...);
  * ``RoutingBackend``     - picks one of the configured providers per model reference.

A model reference is ``provider:model-id``; a bare id uses the default provider.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from hoc.config import LLMSettings, ProviderSettings
from hoc.core.errors import LLMError


@dataclass(frozen=True)
class BackendRequest:
    model: str
    system: str
    user: str
    schema: dict[str, Any]
    schema_name: str
    max_tokens: int


@dataclass(frozen=True)
class BackendResponse:
    output: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0


class LLMBackend(Protocol):
    def complete(self, request: BackendRequest) -> BackendResponse: ...


def _api_key(settings: ProviderSettings, provider: str) -> str | None:
    if settings.api_key_env is None:
        return None
    key = os.environ.get(settings.api_key_env)
    if not key:
        raise LLMError(f"{settings.api_key_env} is not set (needed by provider {provider!r})")
    return key


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a model's JSON answer, tolerating code fences and surrounding prose."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("model did not return JSON") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"model returned invalid JSON: {e}") from None
    if not isinstance(value, dict):
        raise LLMError("model returned JSON that is not an object")
    return value


def _schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "\n\nAnswer with a single JSON object and nothing else. It must conform to this JSON "
        f"schema:\n{json.dumps(schema)}"
    )


class AnthropicBackend:
    """Claude via the official SDK, streamed so long outputs never hit HTTP timeouts.

    ``structured_mode``:
      * ``json_schema`` (default) - native structured outputs (``output_config.format``);
      * ``tool``        - one strict tool the prompt tells the model to call (older models);
      * ``json_prompt`` - schema in the prompt, JSON parsed from the text.
    """

    def __init__(self, settings: ProviderSettings | None = None, name: str = "anthropic") -> None:
        import anthropic  # imported lazily so tests and the UI work without credentials

        self._settings = settings or ProviderSettings(
            kind="anthropic", api_key_env="ANTHROPIC_API_KEY"
        )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=_api_key(self._settings, name), base_url=self._settings.base_url
        )

    def complete(self, request: BackendRequest) -> BackendResponse:
        mode = self._settings.structured_mode
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        if mode == "json_schema":
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": request.schema}}
        elif mode == "tool":
            kwargs["tools"] = [
                {
                    "name": request.schema_name,
                    "description": "Return the result in exactly this structure.",
                    "input_schema": request.schema,
                    "strict": True,
                }
            ]
            kwargs["system"] += f"\n\nAnswer only by calling the {request.schema_name} tool."
        else:
            kwargs["system"] += _schema_instruction(request.schema)
        kwargs = _merge(kwargs, self._settings.extra)

        try:
            with self._client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"cannot reach the Anthropic API: {e}") from e

        if message.stop_reason == "refusal":
            raise LLMError("the model declined this request (stop_reason=refusal)")
        if message.stop_reason == "max_tokens":
            raise LLMError("output was cut off at max_tokens; raise llm.max_tokens in the config")
        usage = (message.usage.input_tokens, message.usage.output_tokens)
        for block in message.content:
            if block.type == "tool_use" and mode == "tool":
                return BackendResponse(dict(block.input), *usage)
            if block.type == "text" and mode != "tool":
                return BackendResponse(parse_json_object(block.text), *usage)
        raise LLMError("model returned no structured output")


class OpenAICompatBackend:
    """Any OpenAI-compatible chat-completions endpoint.

    ``structured_mode``: ``json_schema`` (strict ``response_format``), ``tool`` (forced
    function call) or ``json_prompt`` (schema in the prompt; works with every model).
    """

    def __init__(self, settings: ProviderSettings, name: str = "openai") -> None:
        import openai

        self._settings = settings
        self._openai = openai
        self._client = openai.OpenAI(
            api_key=_api_key(settings, name) or "not-needed", base_url=settings.base_url
        )

    def complete(self, request: BackendRequest) -> BackendResponse:
        mode = self._settings.structured_mode
        system = request.system
        kwargs: dict[str, Any] = {
            "model": request.model,
            self._settings.max_tokens_param: request.max_tokens,
        }
        if mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                },
            }
        elif mode == "tool":
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": request.schema_name,
                        "description": "Return the result in exactly this structure.",
                        "parameters": request.schema,
                    },
                }
            ]
            kwargs["tool_choice"] = {"type": "function", "function": {"name": request.schema_name}}
        else:
            system += _schema_instruction(request.schema)
        kwargs["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": request.user},
        ]
        kwargs = _merge(kwargs, self._settings.extra)

        try:
            completion = self._client.chat.completions.create(**kwargs)
        except self._openai.APIStatusError as e:
            raise LLMError(f"LLM API error {e.status_code}: {e.message}") from e
        except self._openai.APIConnectionError as e:
            raise LLMError(f"cannot reach the LLM endpoint: {e}") from e

        choice = completion.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("output was cut off at max_tokens; raise llm.max_tokens in the config")
        usage = completion.usage
        tokens = (usage.prompt_tokens, usage.completion_tokens) if usage else (0, 0)
        if mode == "tool":
            calls = choice.message.tool_calls or []
            if not calls:
                raise LLMError("model did not call the output tool")
            return BackendResponse(parse_json_object(calls[0].function.arguments), *tokens)
        return BackendResponse(parse_json_object(choice.message.content or ""), *tokens)


BackendFactory = Callable[[ProviderSettings, str], LLMBackend]


def _fixture_backend(settings: ProviderSettings, name: str) -> LLMBackend:
    from hoc.llm.testing import FixtureBackend

    if not settings.path:
        raise LLMError(f"provider {name!r} of kind 'fixture' needs a path")
    delay = settings.extra.get("delay")
    return FixtureBackend(settings.path, None if delay is None else float(delay))


#: provider kind -> constructor. Register a new kind here to support another API family.
BACKEND_KINDS: dict[str, BackendFactory] = {
    "anthropic": AnthropicBackend,
    "openai_compat": OpenAICompatBackend,
    "fixture": _fixture_backend,
}


class RoutingBackend:
    """Resolves ``provider:model`` references and delegates to that provider's backend."""

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._backends: dict[str, LLMBackend] = {}

    def complete(self, request: BackendRequest) -> BackendResponse:
        provider, model = self._settings.split_model(request.model)
        return self._backend(provider).complete(replace(request, model=model))

    def _backend(self, provider: str) -> LLMBackend:
        if provider not in self._backends:
            settings = self._settings.providers.get(provider)
            if settings is None:
                raise LLMError(f"unknown LLM provider {provider!r}; add it under llm.providers")
            self._backends[provider] = BACKEND_KINDS[settings.kind](settings, provider)
        return self._backends[provider]
