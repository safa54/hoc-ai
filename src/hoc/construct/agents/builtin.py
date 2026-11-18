"""Built-in roles: planner, builder, critic, tester, refiner.

Each is one structured LLM call (versioned prompt in ``prompts/agent_<role>/``) plus a little
deterministic post-processing. They are small on purpose: behaviour lives in the prompts,
plumbing lives in the engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hoc.construct.agents.base import (
    AgentResult,
    LLMAgent,
    Proposal,
    QuestionDraft,
    register_agent,
)
from hoc.construct.context import (
    MODE_NOTES,
    BuildContext,
    expert_directives,
    mentioned_concepts,
    render_brief,
    render_feedback,
    render_graph,
    select_sources,
)
from hoc.construct.drafts import OPS_GUIDE, DraftOp
from hoc.construct.validators import redundant_prerequisite_edges, run_validators
from hoc.core.build import (
    AgentTask,
    Area,
    Blueprint,
    Feedback,
    FeedbackKind,
    FeedbackStatus,
    GraphTest,
    MissingEdge,
    TestTrace,
    TraceStep,
)
from hoc.core.common import slugify
from hoc.core.errors import HocError


def _grain(ctx: BuildContext) -> str:
    return (
        ctx.blueprint.grain_guide
        if ctx.blueprint and ctx.blueprint.grain_guide
        else ("One node = one idea a learner can understand, or fail to understand, on its own.")
    )


def _outline(ctx: BuildContext, detail: bool | set[str] = False) -> str:
    return render_graph(
        ctx.graph,
        detail=detail,
        summary_chars=ctx.settings.outline_summary_chars,
        definition_chars=ctx.settings.detail_definition_chars,
    )


_SEVERITY = {"high": 0, "medium": 1, "low": 2}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


# ------------------------------------------------------------------------- planner


class AreaDraft(BaseModel):
    id: str
    title: str
    description: str = ""
    key_points: list[str] = Field(default_factory=list)


class PlannerOutput(BaseModel):
    summary: str
    grain_guide: str
    areas: list[AreaDraft]
    questions: list[QuestionDraft] = Field(default_factory=list)


@register_agent("planner")
class PlannerAgent(LLMAgent):
    """Decomposes the topic into areas and writes the grain guide. Re-plans when the brief,
    sources or expert directives change, keeping the ids of areas that still apply."""

    role = "planner"
    name = "planner"

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult:
        existing = "(none yet)"
        if ctx.blueprint and ctx.blueprint.areas:
            existing = "\n".join(
                f"- {a.id}: {a.title} — {a.description}" for a in ctx.blueprint.areas
            )
        result = ctx.llm.structured(
            "agent_planner",
            {
                "brief": render_brief(ctx.brief),
                "sources": select_sources(
                    ctx.chunks,
                    ctx.brief.title + " " + ctx.brief.description,
                    ctx.settings.source_budget_chars,
                ),
                "blueprint": existing,
                "directives": render_feedback(expert_directives(ctx.feedback)),
                "graph": _outline(ctx),
                "mode_note": MODE_NOTES[ctx.mode],
                "max_areas": ctx.settings.max_areas,
                "area_concepts": ctx.settings.area_concepts,
            },
            PlannerOutput,
            run_id=ctx.run_id,
        )
        out = result.value
        areas: list[Area] = []
        seen: set[str] = set()
        for a in out.areas:
            try:
                aid = slugify(a.id or a.title)
            except ValueError:
                continue
            if aid in seen:
                continue
            seen.add(aid)
            areas.append(
                Area(id=aid, title=a.title, description=a.description, key_points=a.key_points)
            )
        if not areas:
            raise HocError("planner returned no areas")
        return AgentResult(
            summary=f"Planned {len(areas)} areas: " + ", ".join(a.title for a in areas),
            blueprint=Blueprint(summary=out.summary, grain_guide=out.grain_guide, areas=areas),
            questions=self.questions(out.questions),
        )


# ------------------------------------------------------------------------- builder


class BuilderOutput(BaseModel):
    rationale: str
    confidence: float
    ops: list[DraftOp]
    questions: list[QuestionDraft] = Field(default_factory=list)


@register_agent("builder")
class BuilderAgent(LLMAgent):
    """Builds one area: its concepts with content cards, and relations among them and to the
    rest of the graph. Reuses existing concepts instead of duplicating them."""

    role = "builder"
    name = "builder"

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult:
        assert ctx.blueprint is not None
        area = next((a for a in ctx.blueprint.areas if a.id == task.area_id), None)
        if area is None:
            raise HocError(f"area {task.area_id!r} is not in the blueprint")
        others = (
            "\n".join(f"- {a.title}" for a in ctx.blueprint.areas if a.id != area.id) or "(none)"
        )
        area_text = f"{area.title}\n{area.description}\nKey points:\n" + "\n".join(
            f"- {p}" for p in area.key_points
        )
        result = ctx.llm.structured(
            "agent_builder",
            {
                "brief": render_brief(ctx.brief),
                "grain_guide": _grain(ctx),
                "area": area_text,
                "other_areas": others,
                "area_concepts": ctx.settings.area_concepts,
                "graph": _outline(ctx),
                "sources": select_sources(ctx.chunks, area_text, ctx.settings.source_budget_chars),
                "directives": render_feedback(expert_directives(ctx.feedback)),
                "ops_guide": OPS_GUIDE,
                "mode_note": MODE_NOTES[ctx.mode],
            },
            BuilderOutput,
            run_id=ctx.run_id,
        )
        out = result.value
        proposals = []
        if out.ops:
            proposals.append(
                Proposal(
                    drafts=out.ops,
                    rationale=f"Build area '{area.title}': {out.rationale}",
                    confidence=_clamp(out.confidence),
                    llm_call_id=result.call_id,
                    topic_id=area.id,
                )
            )
        return AgentResult(
            summary=f"Built area '{area.title}' ({len(out.ops)} ops)",
            proposals=proposals,
            questions=self.questions(out.questions),
        )


# -------------------------------------------------------------------------- critic


class Finding(BaseModel):
    target_kind: Literal["graph", "concept", "relation"]
    target_id: str = Field(description="concept id or relation id; empty for graph")
    severity: Literal["low", "medium", "high"]
    text: str


class CriticOutput(BaseModel):
    findings: list[Finding]


@register_agent("critic")
class CriticAgent(LLMAgent):
    """Reviews the graph: deterministic checks plus an LLM review of grain, coverage,
    prerequisite correctness and content quality. Produces feedback; the only change it
    proposes itself is dropping prerequisite edges that a chain already implies."""

    role = "critic"
    name = "critic"

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult:
        known: set[tuple[str, str | None, str]] = {
            (f.target_kind, f.target_id, f.text) for f in ctx.feedback
        }
        notes: list[Feedback] = []

        def note(kind: str, target: str | None, severity: str, text: str) -> None:
            if (kind, target, text) in known:
                return
            known.add((kind, target, text))
            notes.append(
                Feedback.model_validate(
                    {
                        "kind": FeedbackKind.ISSUE,
                        "target_kind": kind,
                        "target_id": target,
                        "severity": severity,
                        "text": text,
                        "author": self.actor,
                    }
                )
            )

        # edges implied by a chain are dropped without asking a model (see the proposal below)
        prune = (
            [r for r in redundant_prerequisite_edges(ctx.graph) if not r.locked]
            if ctx.settings.prune_redundant_prerequisites
            else []
        )
        pruned = {r.id for r in prune}
        for issue in run_validators(ctx.graph):
            if issue.check == "redundant_prerequisites" and issue.target_id in pruned:
                continue
            note(issue.target_kind, issue.target_id, issue.severity, issue.text)

        concepts = ctx.graph.concepts()
        budget = ctx.settings.area_concepts
        sizes: dict[str, int] = {}
        for c in concepts:
            if c.topic_ids:
                sizes[c.topic_ids[0]] = sizes.get(c.topic_ids[0], 0) + 1
        for area_id, n in sorted(sizes.items()):
            if n > 2 * budget:
                note(
                    "graph",
                    None,
                    "medium",
                    f"Area '{area_id}' has {n} concepts (the aim is at most {budget}). Merge "
                    "near-duplicates and fold minor points into the cards of their parent "
                    "concepts.",
                )

        # the model looks only at what changed since its last review; an unchanged graph costs
        # no call at all
        pending = sorted(
            (c for c in concepts if ctx.reviewed_versions.get(c.id) != c.version),
            key=lambda c: (c.topic_ids[:1], c.id),  # one area at a time
        )
        changed = {c.id for c in pending[: ctx.settings.review_batch]}
        reviewed: list[str] | None = [] if len(pending) > len(changed) else None
        if concepts and ctx.graph_seq != ctx.reviewed_seq:
            if reviewed is not None:  # a large graph is reviewed over several rounds
                reviewed = sorted(changed)
            open_notes = [f for f in ctx.feedback if f.status is not FeedbackStatus.DISMISSED]
            n_areas = len(ctx.blueprint.areas) if ctx.blueprint else max(len(sizes), 1)
            result = ctx.llm.structured(
                "agent_critic",
                {
                    "brief": render_brief(ctx.brief),
                    "grain_guide": _grain(ctx),
                    "graph": _outline(ctx, changed),
                    "size_note": (
                        f"{len(concepts)} concepts in {n_areas} areas; the aim is at most "
                        f"{budget} per area."
                    ),
                    "max_findings": ctx.settings.max_findings,
                    "known_feedback": render_feedback(
                        open_notes[-ctx.settings.known_feedback_limit :],
                        text_chars=ctx.settings.known_feedback_chars,
                    ),
                },
                CriticOutput,
                run_id=ctx.run_id,
            )
            concept_ids = {c.id for c in concepts}
            relation_ids = {r.id for r in ctx.graph.relations()}
            findings = sorted(result.value.findings, key=lambda f: _SEVERITY[f.severity])
            for f in findings[: ctx.settings.max_findings]:
                target: str | None = f.target_id.strip() or None
                kind: str = f.target_kind
                if kind == "concept" and target not in concept_ids:
                    kind, target = "graph", None
                if kind == "relation" and target not in relation_ids:
                    kind, target = "graph", None
                note(kind, target, f.severity, f.text.strip())

        proposals = []
        if prune:
            proposals.append(
                Proposal(
                    drafts=[
                        DraftOp(
                            op="remove_relation",
                            relation_type="prerequisite_of",
                            source=r.source,
                            target=r.target,
                            rationale="implied by a longer prerequisite chain",
                        )
                        for r in prune
                    ],
                    rationale=f"Remove {len(prune)} prerequisite edge(s) that already follow "
                    "from a chain of other prerequisites",
                    confidence=0.95,
                )
            )
        return AgentResult(
            summary=f"Reviewed the graph: {len(notes)} new findings"
            + (f", {len(prune)} redundant edges" if prune else ""),
            feedback=notes,
            proposals=proposals,
            reviewed=reviewed,
        )


# -------------------------------------------------------------------------- tester


class TestDraft(BaseModel):
    question: str
    expected: str


class TestsOutput(BaseModel):
    tests: list[TestDraft]


class StepDraft(BaseModel):
    text: str
    concept_ids: list[str]
    missing: list[str] = Field(description="ideas this step needs that the graph lacks")


class EdgeDraft(BaseModel):
    source: str
    target: str
    reason: str


class TraceOutput(BaseModel):
    steps: list[StepDraft]
    missing_edges: list[EdgeDraft]
    verdict: Literal["covered", "gaps", "not_covered"]
    summary: str


@register_agent("tester")
class TesterAgent(LLMAgent):
    """Writes test questions, and traces a question through the graph: which concepts does
    each solution step use, and what is missing? Gaps become feedback on the graph."""

    role = "tester"
    name = "tester"
    __test__ = False

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult:
        if task.test_id is None:
            return self._generate(ctx)
        return self._trace(task.test_id, ctx)

    def _generate(self, ctx: BuildContext) -> AgentResult:
        count = max(ctx.settings.min_tests - len(ctx.tests), 3)
        existing = "\n".join(f"- {t.question}" for t in ctx.tests) or "(none)"
        result = ctx.llm.structured(
            "agent_tester_generate",
            {
                "brief": render_brief(ctx.brief),
                "graph": _outline(ctx),
                "existing_tests": existing,
                "sources": select_sources(
                    ctx.chunks,
                    "exercise problem solution example",
                    ctx.settings.source_budget_chars // 2,
                ),
                "count": count,
            },
            TestsOutput,
            run_id=ctx.run_id,
        )
        tests = [
            GraphTest(question=t.question.strip(), expected=t.expected.strip(), origin="agent")
            for t in result.value.tests
            if t.question.strip()
        ]
        return AgentResult(summary=f"Wrote {len(tests)} test questions", tests=tests)

    def _trace(self, test_id: str, ctx: BuildContext) -> AgentResult:
        test = next((t for t in ctx.tests if t.id == test_id), None)
        if test is None:
            raise HocError(f"test {test_id!r} not found")
        result = ctx.llm.structured(
            "agent_tester_trace",
            {
                "brief": render_brief(ctx.brief),
                "graph": _outline(ctx),
                "question": test.question,
                "expected": test.expected or "(not given)",
            },
            TraceOutput,
            run_id=ctx.run_id,
        )
        out = result.value
        concept_ids = {c.id for c in ctx.graph.concepts()}
        steps = []
        for s in out.steps:
            unknown = [c for c in s.concept_ids if c not in concept_ids]
            steps.append(
                TraceStep(
                    text=s.text,
                    concept_ids=[c for c in s.concept_ids if c in concept_ids],
                    missing=[*s.missing, *unknown],
                )
            )
        edges = [
            MissingEdge(source=e.source, target=e.target, reason=e.reason)
            for e in out.missing_edges
            if e.source in concept_ids and e.target in concept_ids
        ]
        trace = TestTrace(
            test_id=test.id,
            graph_seq=ctx.graph_seq,
            steps=steps,
            missing_edges=edges,
            verdict=out.verdict,
            summary=out.summary,
            author=self.actor,
        )
        notes: list[Feedback] = []
        short = test.question if len(test.question) < 90 else test.question[:87] + "..."
        missing = list(dict.fromkeys(m.strip() for s in steps for m in s.missing if m.strip()))
        if missing:
            notes.append(
                Feedback(
                    kind=FeedbackKind.ISSUE,
                    target_kind="test",
                    target_id=test.id,
                    severity="high" if out.verdict == "not_covered" else "medium",
                    text=f'Test "{short}" needs ideas the graph lacks: ' + "; ".join(missing),
                    author=self.actor,
                )
            )
        for e in edges:
            notes.append(
                Feedback(
                    kind=FeedbackKind.SUGGESTION,
                    target_kind="concept",
                    target_id=e.target,
                    severity="medium",
                    text=f'Test "{short}" suggests a missing relation {e.source} -> '
                    f"{e.target}: {e.reason}",
                    author=self.actor,
                )
            )
        return AgentResult(
            summary=f'Traced "{short}": {out.verdict}', traces=[trace], feedback=notes
        )


# ------------------------------------------------------------------------- refiner


class ProposalDraft(BaseModel):
    addresses: list[str] = Field(description="ids of the feedback items this resolves")
    rationale: str
    confidence: float
    ops: list[DraftOp]


class Dismissal(BaseModel):
    feedback_id: str
    reason: str


class RefinerOutput(BaseModel):
    proposals: list[ProposalDraft]
    dismissals: list[Dismissal]
    questions: list[QuestionDraft] = Field(default_factory=list)


@register_agent("refiner")
class RefinerAgent(LLMAgent):
    """Turns feedback (from the expert, the critic and the tester) into concrete proposals:
    edits, splits, merges, new concepts and relations — or explains why a note needs none."""

    role = "refiner"
    name = "refiner"

    def run(self, task: AgentTask, ctx: BuildContext) -> AgentResult:
        items = ctx.feedback_by_id(task.feedback_ids)
        if not items:
            return AgentResult(summary="Nothing to refine")
        query = " ".join(f.text for f in items)
        # full cards only for the concepts these notes are about; the rest stays an outline
        scope = mentioned_concepts(ctx.graph, items)
        for f in items:
            trace = ctx.traces.get(f.target_id or "") if f.target_kind == "test" else None
            if trace is not None:
                scope.update(cid for step in trace.steps for cid in step.concept_ids)
        result = ctx.llm.structured(
            "agent_refiner",
            {
                "brief": render_brief(ctx.brief),
                "grain_guide": _grain(ctx),
                "graph": _outline(ctx, scope),
                "feedback": render_feedback(items),
                "directives": render_feedback(
                    [d for d in expert_directives(ctx.feedback) if d.id not in task.feedback_ids]
                ),
                "sources": select_sources(ctx.chunks, query, ctx.settings.source_budget_chars // 2),
                "ops_guide": OPS_GUIDE,
                "mode_note": MODE_NOTES[ctx.mode],
            },
            RefinerOutput,
            run_id=ctx.run_id,
        )
        out = result.value
        ids = {f.id for f in items}
        proposals = [
            Proposal(
                drafts=p.ops,
                rationale=p.rationale,
                confidence=_clamp(p.confidence),
                addresses=[a for a in p.addresses if a in ids],
                llm_call_id=result.call_id,
            )
            for p in out.proposals
            if p.ops
        ]
        dismissals = [(d.feedback_id, d.reason) for d in out.dismissals if d.feedback_id in ids]
        return AgentResult(
            summary=f"Worked on {len(items)} notes: {len(proposals)} proposals, "
            f"{len(dismissals)} dismissed",
            proposals=proposals,
            dismissals=dismissals,
            questions=self.questions(out.questions),
        )
