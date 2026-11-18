"""Exercise agents (LEARNING_SIDE_PLAN L7.1): ``extractor`` reads an uploaded exercise sheet and
splits it into tasks; ``matcher`` links tasks to the concepts needed to solve them.

Both only return ``Task`` records (assessment layer). They never change the knowledge graph:
ideas the graph lacks become ``Feedback`` for the refiner, the same loop the tester feeds.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from hoc.construct.agents.base import AgentResult, LLMAgent, register_agent
from hoc.construct.context import BuildContext, clip, render_brief, shortlist
from hoc.core.assessment import Approach, Requirement, Rubric, Task, TaskStatus, TaskType
from hoc.core.build import AgentTask, Feedback, FeedbackKind
from hoc.core.errors import HocError
from hoc.core.refs import Facet, TargetKind, TargetRef

_WS = re.compile(r"\s+")


def _key(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


# ------------------------------------------------------------------ extractor


class ExtractedTask(BaseModel):
    title: str
    prompt: str
    solution: str
    task_type: Literal[
        "mcq",
        "numeric",
        "short_answer",
        "explain_why",
        "compare",
        "transfer_problem",
        "worked_problem",
    ]
    options: list[str] = Field(default_factory=list)
    correct_answers: list[str] = Field(default_factory=list)


class ExtractOutput(BaseModel):
    tasks: list[ExtractedTask]


def _parts(texts: list[str], budget: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for t in texts:
        if current and len(current) + len(t) > budget:
            parts.append(current)
            current = t
        else:
            current = f"{current}\n\n{t}" if current else t
    if current:
        parts.append(current)
    return parts


@register_agent("extractor")
class ExtractorAgent(LLMAgent):
    role = "extractor"
    name = "extractor"

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult:
        if task.source_id is None:
            raise HocError("the extractor needs a source")
        chunks = [c for c in ctx.material_chunks if c.source_id == task.source_id]
        parts = _parts([c.text for c in chunks], ctx.settings.source_budget_chars)
        known = {_key(t.prompt) for t in ctx.tasks}
        found: list[Task] = []
        for i, part in enumerate(parts, 1):
            result = ctx.llm.structured(
                "agent_task_extract",
                {
                    "language": ctx.brief.language,
                    "topic": ctx.brief.title or "(untitled)",
                    "part": f"{i}/{len(parts)}",
                    "material": part,
                },
                ExtractOutput,
                run_id=ctx.run_id,
            )
            for e in result.value.tasks:
                if not e.prompt.strip() or _key(e.prompt) in known:
                    continue
                known.add(_key(e.prompt))
                found.append(
                    Task(
                        title=e.title.strip(),
                        prompt=e.prompt.strip(),
                        task_type=TaskType(e.task_type),
                        options=[o for o in e.options if o.strip()],
                        solution_text=e.solution.strip(),
                        rubric=Rubric(correct_answers=[a for a in e.correct_answers if a.strip()]),
                        origin="source",
                        status=TaskStatus.DRAFT,
                        source_id=task.source_id,
                        match_status="unmatched",
                    )
                )
        return AgentResult(summary=f"Extracted {len(found)} exercises", tasks=found)


# -------------------------------------------------------------------- matcher


class MatchReq(BaseModel):
    concept_id: str
    facet: Literal["recall", "apply", "explain", "transfer"]
    weight: Literal["primary", "secondary"]


class MatchApproach(BaseModel):
    label: str
    requirements: list[MatchReq]


class MatchItem(BaseModel):
    index: int
    approaches: list[MatchApproach]
    confidence: float
    unmapped: list[str]


class MatchOutput(BaseModel):
    items: list[MatchItem]


@register_agent("matcher")
class MatcherAgent(LLMAgent):
    role = "matcher"
    name = "matcher"

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult:
        by_id = {t.id: t for t in ctx.tasks}
        tasks = [by_id[i] for i in task.task_ids if i in by_id]
        if not tasks:
            return AgentResult(summary="No exercises to match")
        graph = ctx.graph
        names = {c.id: c for c in graph.concepts()}
        short: list[list[str]] = []
        blocks: list[str] = []
        for i, t in enumerate(tasks):
            ids = shortlist(graph, f"{t.title} {t.prompt}")
            short.append(ids)
            cand = "\n".join(
                f"  - {cid} | {names[cid].name}: {clip(names[cid].card.one_line, 110)}"
                for cid in ids
            )
            blocks.append(
                f"[{i}] {t.title or 'Exercise'}: {clip(t.prompt, 700)}\nCandidates:\n{cand}"
            )
        areas: dict[str, int] = {}
        for c in graph.concepts():
            area = c.topic_ids[0] if c.topic_ids else "other"
            areas[area] = areas.get(area, 0) + 1
        outline = "\n".join(f"- {a}: {n} concepts" for a, n in areas.items()) or "(empty)"
        result = ctx.llm.structured(
            "agent_task_match",
            {"topic": render_brief(ctx.brief), "outline": outline, "items": "\n\n".join(blocks)},
            MatchOutput,
            run_id=ctx.run_id,
        )
        by_index = {m.index: m for m in result.value.items}
        updated: list[Task] = []
        notes: list[Feedback] = []
        wanted: set[str] = set()
        for i, t in enumerate(tasks):
            m = by_index.get(i)
            allowed = set(short[i])
            approaches: list[Approach] = []
            for j, a in enumerate(m.approaches if m else [], 1):
                merged: dict[tuple[str, str], Requirement] = {}
                for r in a.requirements:
                    if r.concept_id not in allowed:
                        continue
                    key = (r.concept_id, r.facet)
                    old = merged.get(key)
                    if old is None or (old.weight == "secondary" and r.weight == "primary"):
                        merged[key] = Requirement(
                            ref=TargetRef(
                                kind=TargetKind.CONCEPT, id=r.concept_id, facet=Facet(r.facet)
                            ),
                            weight=r.weight,
                        )
                if merged:  # the same concept listed twice is one requirement
                    approaches.append(
                        Approach(id=f"a{j}", label=a.label, requirements=list(merged.values()))
                    )
            confidence = max(0.0, min(1.0, m.confidence)) if m and approaches else 0.0
            unmapped = [u.strip() for u in (m.unmapped if m else []) if u.strip()]
            updated.append(
                t.model_copy(
                    update={
                        "approaches": approaches,
                        "match_confidence": confidence,
                        "match_status": "proposed",
                        "unmapped": unmapped,
                    }
                )
            )
            for u in unmapped:
                if _key(u) in wanted:
                    continue
                wanted.add(_key(u))
                notes.append(
                    Feedback(
                        kind=FeedbackKind.ISSUE,
                        target_kind="graph",
                        severity="medium",
                        text=f'Exercise "{clip(t.title or t.prompt, 70)}" needs an idea the '
                        f"graph lacks: {u}",
                        author=self.actor,
                    )
                )
        return AgentResult(
            summary=f"Matched {len(updated)} exercises ({sum(1 for t in updated if t.approaches)} "
            f"with concepts)",
            tasks=updated,
            feedback=notes,
        )
