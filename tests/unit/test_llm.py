import pytest
from pydantic import BaseModel

from hoc.config import LLMSettings, load_settings
from hoc.core.errors import LLMError, PromptError
from hoc.llm import LLMClient, PromptRegistry, usage_summary
from hoc.llm.prompts import parse_template
from hoc.llm.testing import ScriptedBackend


class Out(BaseModel):
    text: str


@pytest.fixture
def prompts():
    return PromptRegistry("prompts")


def make_client(db, backend, prompts, **overrides):
    settings = load_settings().llm.model_copy(update=overrides)
    return LLMClient(backend, db, settings, prompts)


def test_prompt_render_and_validation():
    t = parse_template("s", "v1", "# SYSTEM\nhi {{a}}\n# USER\n{{b}} and {{a}}")
    r = t.render({"a": 1, "b": "x"})
    assert (r.system, r.user) == ("hi 1", "x and 1")
    with pytest.raises(PromptError, match="missing"):
        t.render({"a": 1})
    with pytest.raises(PromptError, match="unused"):
        t.render({"a": 1, "b": 2, "c": 3})
    with pytest.raises(PromptError):
        parse_template("s", "v1", "# USER\nonly user")


def test_registry_picks_highest_version_unless_pinned(tmp_path):
    for v in ("v1", "v2", "v10"):
        (tmp_path / "s").mkdir(exist_ok=True)
        (tmp_path / "s" / f"{v}.md").write_text(f"# SYSTEM\n{v}\n# USER\nu")
    assert PromptRegistry(tmp_path).get("s").version == "v10"
    assert PromptRegistry(tmp_path, {"s": "v2"}).get("s").version == "v2"


def test_structured_call_logs_and_caches(db, prompts):
    backend = ScriptedBackend([{"text": "hi hi"}])
    client = make_client(db, backend, prompts)
    vars_ = {"word": "hi", "times": 2}
    first = client.structured("smoke", vars_, Out)
    second = client.structured("smoke", vars_, Out)
    assert first.value == second.value == Out(text="hi hi")
    assert (first.cached, second.cached) == (False, True)
    assert len(backend.requests) == 1  # second call never hit the backend
    assert first.call_id != second.call_id
    (row,) = usage_summary(db)
    assert row["calls"] == 2 and row["cache_hits"] == 1


def test_changing_inputs_invalidates_cache(db, prompts):
    backend = ScriptedBackend(responder=lambda r: {"text": r.user})
    client = make_client(db, backend, prompts)
    client.structured("smoke", {"word": "a", "times": 1}, Out)
    client.structured("smoke", {"word": "b", "times": 1}, Out)
    assert len(backend.requests) == 2


def test_cache_can_be_disabled(db, prompts):
    backend = ScriptedBackend([{"text": "1"}, {"text": "2"}])
    client = make_client(db, backend, prompts, cache_enabled=False)
    client.structured("smoke", {"word": "a", "times": 1}, Out)
    client.structured("smoke", {"word": "a", "times": 1}, Out)
    assert len(backend.requests) == 2


def test_retry_feeds_validation_error_back(db, prompts):
    backend = ScriptedBackend([{"wrong": 1}, {"text": "ok"}])
    client = make_client(db, backend, prompts)
    res = client.structured("smoke", {"word": "a", "times": 1}, Out)
    assert res.value.text == "ok"
    assert "failed validation" in backend.requests[1].user
    assert "failed validation" not in backend.requests[0].user


def test_gives_up_after_max_attempts_and_logs_error(db, prompts):
    backend = ScriptedBackend([{"wrong": 1}, {"wrong": 2}])
    client = make_client(db, backend, prompts)
    with pytest.raises(LLMError):
        client.structured("smoke", {"word": "a", "times": 1}, Out)
    with db.read() as conn:
        (n,) = conn.execute(
            'SELECT COUNT(*) FROM llm_calls WHERE data LIKE \'%"error":"%\''
        ).fetchone()
    assert n == 1


def test_step_uses_configured_tier_and_cost_tracking(db, prompts):
    backend = ScriptedBackend([{"text": "x"}])
    base = load_settings().llm.model_dump()
    base["pricing"] = {"claude-haiku-5-5": {"input_per_mtok": 1.0, "output_per_mtok": 5.0}}
    settings = LLMSettings.model_validate(base)
    client = LLMClient(backend, db, settings, prompts)
    client.structured("smoke", {"word": "a", "times": 1}, Out)
    assert backend.requests[0].model == "claude-haiku-5-5"  # _smoke -> fast tier
    (row,) = usage_summary(db)
    assert row["cost_usd"] == pytest.approx((100 * 1.0 + 20 * 5.0) / 1e6)
