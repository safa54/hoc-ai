"""Real API smoke test. Skipped unless HOC_LIVE=1 and ANTHROPIC_API_KEY is set."""

import os

import pytest
from pydantic import BaseModel

from hoc.services import HocApp

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("HOC_LIVE") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
        reason="set HOC_LIVE=1 and ANTHROPIC_API_KEY to run",
    ),
]


class Out(BaseModel):
    text: str


def test_structured_call_against_real_model(tmp_path):
    app = HocApp.open(db_path=tmp_path / "live.db")
    res = app.llm.structured("smoke", {"word": "ok", "times": 2}, Out)
    assert "ok" in res.value.text
