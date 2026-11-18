"""Provenance: sources, chunks, LLM calls, runs, evidence links (ARCHITECTURE §4.2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from hoc.core.common import StrictModel, new_id, utc_now


class SourceType(StrEnum):
    TEXTBOOK = "textbook"
    NOTES = "notes"
    PAPER = "paper"
    PROBLEM_SET = "problem_set"
    WEB = "web"


class Source(StrictModel):
    id: str = Field(default_factory=lambda: new_id("src"))
    title: str
    type: SourceType
    file_hash: str
    path: str | None = None
    licence_note: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class ChunkRole(StrEnum):
    DEFINITION = "definition"
    THEOREM = "theorem"
    EXPOSITION = "exposition"
    EXAMPLE = "example"
    EXERCISE = "exercise"
    SOLUTION = "solution"
    OTHER = "other"


class Chunk(StrictModel):
    id: str = Field(default_factory=lambda: new_id("chk"))
    source_id: str
    locator: str  # e.g. "ch2 §2.3 p.41"
    text: str
    role: ChunkRole = ChunkRole.OTHER
    ordinal: int = 0


class EvidenceLink(StrictModel):
    """Connects a graph element to the thing that supports (or contradicts) it."""

    target_kind: Literal["concept", "relation", "misconception", "task"]
    target_id: str
    chunk_id: str | None = None
    llm_call_id: str | None = None
    review_id: str | None = None
    quote: str | None = None
    stance: Literal["supports", "contradicts"] = "supports"


class LLMCall(StrictModel):
    id: str = Field(default_factory=lambda: new_id("llm"))
    run_id: str | None = None
    step: str
    model: str
    prompt_version: str
    input_hash: str
    system: str
    user: str
    output: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    cached: bool = False
    attempts: int = 1
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Run(StrictModel):
    """One pipeline or evaluation run, with a config snapshot for reproducibility."""

    id: str = Field(default_factory=lambda: new_id("run"))
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    code_version: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    outcome: Literal["running", "ok", "failed"] = "running"
