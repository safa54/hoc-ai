"""What an agent sees: a read-only snapshot of the workspace plus an LLM client.

Rendering helpers turn the graph, feedback and sources into compact prompt text so every
agent describes the world the same way.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field

from hoc.config import AgentSettings
from hoc.core.assessment import Task
from hoc.core.build import (
    Blueprint,
    Brief,
    Feedback,
    FeedbackKind,
    FeedbackStatus,
    GraphTest,
    RunMode,
    TestTrace,
)
from hoc.core.knowledge import Necessity
from hoc.core.provenance import Chunk
from hoc.llm import LLMClient
from hoc.store.knowledge_graph import KnowledgeGraph


@dataclass
class BuildContext:
    graph: KnowledgeGraph
    brief: Brief
    blueprint: Blueprint | None
    chunks: list[Chunk]
    feedback: list[Feedback]
    tests: list[GraphTest]
    traces: dict[str, TestTrace]
    tasks: list[Task]
    material_chunks: list[Chunk]  # exercise sheets: never shown to planner/builders
    mode: RunMode
    settings: AgentSettings
    llm: LLMClient
    run_id: str | None = None
    graph_seq: int = 0
    extra: dict[str, str] = field(default_factory=dict)
    reviewed_seq: int = -1  # graph seq at the critic's last review (-1: never reviewed)
    reviewed_versions: dict[str, int] = field(default_factory=dict)  # concept id -> version

    def feedback_by_id(self, ids: list[str]) -> list[Feedback]:
        wanted = set(ids)
        return [f for f in self.feedback if f.id in wanted]

    def open_feedback(self) -> list[Feedback]:
        return [f for f in self.feedback if f.status is FeedbackStatus.OPEN]


# ---------------------------------------------------------------------------- render


def render_brief(brief: Brief) -> str:
    lines = [f"Topic: {brief.title or '(untitled)'}"]
    if brief.audience:
        lines.append(f"Audience / level: {brief.audience}")
    if brief.description:
        lines.append(f"Scope and aspects requested by the expert:\n{brief.description}")
    lines.append(f"Write all names and text in {brief.language}.")
    return "\n".join(lines)


def render_graph(
    graph: KnowledgeGraph,
    *,
    detail: bool | Collection[str] = False,
    summary_chars: int = 90,
    definition_chars: int = 400,
) -> str:
    """The graph as prompt text. Every concept gets one outline line; only the concepts in
    ``detail`` (True = all) also get their definition and key ideas. Relations are grouped
    by source, so the text grows with what an agent has to look at, not with the graph."""
    concepts = graph.concepts()
    if not concepts:
        return "(the graph is empty)"
    full = {c.id for c in concepts} if detail is True else set(detail or ())
    lines = [f"CONCEPTS ({len(concepts)}) — id | name: summary"]
    for c in concepts:
        flags = " [expert-locked]" if c.locked else ""
        shown = c.id in full
        summary = c.card.one_line if shown else clip(c.card.one_line, summary_chars)
        lines.append(f"- {c.id} | {c.name}: {summary}{flags}")
        if shown:
            if c.card.definition:
                lines.append(f"    definition: {clip(c.card.definition, definition_chars)}")
            for k in c.card.key_ideas:
                lines.append(f"    {k.id}: {k.text}")
    relations = graph.relations()
    lines.append(
        f"\nRELATIONS ({len(relations)}) — type: source -> targets"
        " (~ = supportive prerequisite, ! = expert-locked)"
    )
    grouped: dict[tuple[str, str], list[str]] = {}
    for r in relations:
        mark = "~" if r.necessity is Necessity.SUPPORTIVE else ""
        lock = "!" if r.locked else ""
        grouped.setdefault((r.type.value, r.source), []).append(f"{mark}{r.target}{lock}")
    for (type_, source), targets in sorted(grouped.items()):
        lines.append(f"- {type_}: {source} -> {', '.join(targets)}")
    return "\n".join(lines)


def mentioned_concepts(graph: KnowledgeGraph, items: list[Feedback]) -> set[str]:
    """Concept ids a batch of notes is about: their targets, both ends of targeted relations,
    and every concept id that appears in a note's text."""
    ids = {c.id for c in graph.concepts()}
    ends = {r.id: (r.source, r.target) for r in graph.relations()}
    found: set[str] = set()
    for f in items:
        if f.target_id in ids:
            found.add(str(f.target_id))
        elif f.target_id in ends:
            found.update(ends[str(f.target_id)])
        found.update(t for t in _ID_TOKEN.findall(f.text.lower()) if t in ids)
    return found


def render_feedback(items: list[Feedback], *, text_chars: int = 0) -> str:
    """Notes as prompt lines; ``text_chars`` > 0 clips each note (for "already known" lists)."""
    if not items:
        return "(none)"
    lines = []
    for f in items:
        target = f.target_kind if f.target_id is None else f"{f.target_kind} {f.target_id}"
        who = "EXPERT" if f.author.is_human else f"agent {f.author.name}"
        text = clip(f.text, text_chars) if text_chars else f.text
        lines.append(f"- [{f.id}] {f.kind.value} on {target} by {who} ({f.severity}): {text}")
        if f.resolution and f.attempts:
            lines.append(f"    earlier attempt: {f.resolution}")
    return "\n".join(lines)


def expert_directives(feedback: list[Feedback]) -> list[Feedback]:
    """Graph-wide expert instructions, which every agent should respect."""
    return [
        f
        for f in feedback
        if f.author.is_human
        and f.target_kind == "graph"
        and f.kind in (FeedbackKind.DIRECTIVE, FeedbackKind.ANSWER)
        and f.status is not FeedbackStatus.DISMISSED
    ]


_WORD = re.compile(r"[a-z0-9]{3,}")
_ID_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def select_sources(chunks: list[Chunk], query: str, budget: int) -> str:
    """Source excerpts within ``budget`` characters: everything if it fits, otherwise the
    chunks sharing most words with ``query``, in document order."""
    if not chunks:
        return "(no source material provided; rely on standard curriculum knowledge)"
    total = sum(len(c.text) for c in chunks)
    chosen = chunks
    if total > budget:
        words = set(_WORD.findall(query.lower()))
        scored = sorted(
            chunks,
            key=lambda c: -len(words & set(_WORD.findall(c.text.lower()))),
        )
        keep: set[str] = set()
        used = 0
        for c in scored:
            if used + len(c.text) > budget:
                continue
            keep.add(c.id)
            used += len(c.text)
        chosen = [c for c in chunks if c.id in keep]
    return "\n\n".join(f"[{c.locator}]\n{c.text}" for c in chosen)


_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "are",
        "was",
        "were",
        "from",
        "into",
        "what",
        "which",
        "when",
        "then",
        "than",
        "have",
        "has",
        "had",
        "not",
        "you",
        "your",
        "can",
        "will",
        "its",
        "their",
        "his",
        "her",
        "over",
        "under",
        "about",
    }
)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def shortlist(graph: KnowledgeGraph, text: str, k: int = 12) -> list[str]:
    """Candidate concept ids for a piece of text by lexical overlap (names count triple).
    Keeps the matcher's prompt small instead of dumping the whole graph into it."""
    wanted = _words(text)
    scored: list[tuple[float, str]] = []
    for c in graph.concepts():
        name_words = _words(c.name + " " + " ".join(c.aliases))
        other = _words(c.card.one_line + " " + " ".join(i.text for i in c.card.key_ideas))
        score = 3 * len(wanted & name_words) + len(wanted & other)
        if score > 0:
            scored.append((score, c.id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    ids = [cid for _, cid in scored[:k]]
    if len(ids) < min(k, 6):  # nothing lexical to go on: offer the roots of the graph
        for cid in graph.learning_order():
            if cid not in ids:
                ids.append(cid)
            if len(ids) >= min(k, 6):
                break
    return ids


MODE_NOTES = {
    RunMode.AUTO: (
        "The expert is not reviewing step by step. Do not ask questions; make the best "
        "decision yourself and explain it in the rationale."
    ),
    RunMode.ASK: (
        "If a decision genuinely depends on the expert's intent (scope, level, framing), you "
        "may ask a short question with 2-4 answer options. Ask rarely; otherwise decide."
    ),
    RunMode.MANUAL: (
        "The expert reviews every proposal. You may ask a short question with 2-4 answer "
        "options when a decision depends on the expert's intent."
    ),
}
