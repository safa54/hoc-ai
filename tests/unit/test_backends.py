import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from hoc.config import LLMSettings, ProviderSettings, load_settings
from hoc.core.errors import LLMError
from hoc.llm.backend import (
    BackendRequest,
    OpenAICompatBackend,
    RoutingBackend,
    parse_json_object,
)
from hoc.llm.schema import portable_schema


def req(model="m"):
    return BackendRequest(model, "sys", "user", {"type": "object"}, "Out", 100)


def test_parse_json_object_tolerates_fences_and_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"a": {"b": 2}} Hope that helps') == {"a": {"b": 2}}
    for bad in ("no json", "[1, 2]", "{broken"):
        with pytest.raises(LLMError):
            parse_json_object(bad)


def test_portable_schema_is_strict_and_unconstrained():
    class Inner(BaseModel):
        n: int = Field(default=1, ge=0, le=5)

    class Outer(BaseModel):
        name: str = Field(default="", max_length=10)
        inner: Inner
        items: list[Inner] = Field(default_factory=list)

    schema = portable_schema(Outer.model_json_schema())

    def walk(node):
        if isinstance(node, dict):
            assert not ({"minimum", "maximum", "default", "title", "maxLength"} & set(node))
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert set(schema["required"]) == {"name", "inner", "items"}


def fake_openai(payload, *, finish="stop", use_tool=False):
    message = SimpleNamespace(
        content=None if use_tool else payload,
        tool_calls=[SimpleNamespace(function=SimpleNamespace(arguments=payload))]
        if use_tool
        else None,
    )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return completion

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


@pytest.mark.parametrize("mode", ["json_schema", "tool", "json_prompt"])
def test_openai_compat_modes(mode):
    backend = OpenAICompatBackend(ProviderSettings(kind="openai_compat", structured_mode=mode))
    backend._client, calls = fake_openai(json.dumps({"x": 1}), use_tool=mode == "tool")
    out = backend.complete(req())
    assert out.output == {"x": 1} and (out.input_tokens, out.output_tokens) == (7, 3)
    sent = calls[0]
    assert ("response_format" in sent) == (mode == "json_schema")
    assert ("tools" in sent) == (mode == "tool")
    assert ("JSON schema" in sent["messages"][0]["content"]) == (mode == "json_prompt")
    assert sent["max_completion_tokens"] == 100


def test_openai_compat_truncation_is_an_error():
    backend = OpenAICompatBackend(ProviderSettings(kind="openai_compat"))
    backend._client, _ = fake_openai('{"x": 1}', finish="length")
    with pytest.raises(LLMError, match="cut off"):
        backend.complete(req())


def test_routing_splits_provider_and_model():
    s = load_settings().llm
    assert s.split_model("openai:gpt-5.1") == ("openai", "gpt-5.1")
    assert s.split_model("ollama:qwen3:32b") == ("ollama", "qwen3:32b")
    assert s.split_model("claude-opus-5-5") == (s.default_provider, "claude-opus-5-5")
    assert s.split_model("unknown:thing") == (s.default_provider, "unknown:thing")


def test_routing_backend_uses_fixture_provider_and_rejects_unknown(tmp_path):
    fixture = tmp_path / "f.json"
    fixture.write_text(json.dumps({"Out": [{"ok": True}]}))
    settings = LLMSettings(
        providers={"demo": ProviderSettings(kind="fixture", path=str(fixture))},
        default_provider="demo",
        models={"standard": "demo:any"},
    )
    router = RoutingBackend(settings)
    assert router.complete(req("demo:any")).output == {"ok": True}
    settings2 = settings.model_copy(update={"default_provider": "nope"})
    with pytest.raises(LLMError, match="unknown LLM provider"):
        RoutingBackend(settings2).complete(req("whatever"))
