"""Context assembly (requirement 5: cost-aware, no full-graph dumps).

The tutor sees a *neighbourhood*: the focus card, direct neighbours, an outline of areas, the
learner's relevant beliefs, a few suggested moves and the last few turns. Each section has a
hard character budget from ``learn.context``; sizes are recorded on the turn for inspection.
With no focus (overview scope) it sees a names-only map of the whole topic instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hoc.config import ContextSettings
from hoc.core.knowledge import Concept, RelationType
from hoc.core.learner import Turn
from hoc.core.learning import BeliefStatus, LearnerNotes, LearnerState, Move, PendingCheck
from hoc.store.knowledge_graph import KnowledgeGraph

_MARK = {
    BeliefStatus.UNKNOWN: " ",
    BeliefStatus.LIKELY_NOT: "!",
    BeliefStatus.UNCERTAIN: "~",
    BeliefStatus.LIKELY: "+",
    BeliefStatus.MASTERED: "++",
}
_ORDER = (
    "focus",
    "prerequisites",
    "leads_to",
    "bridges",
    "outline",
    "map",
    "learner",
    "moves",
    "pending_check",
    "exercises",
    "task",
    "thread",
)
_TITLES = {
    "focus": "FOCUS CONCEPT",
    "prerequisites": "PREREQUISITES (learn first)",
    "leads_to": "THIS LEADS TO",
    "bridges": "RELATED CONCEPTS",
    "outline": "TOPIC OUTLINE (areas, understood/total)",
    "map": "WHOLE-TOPIC MAP ([+]=understood [~]=shaky [!]=struggling [ ]=unknown)",
    "learner": "WHAT THE SYSTEM BELIEVES ABOUT THE LEARNER",
    "moves": "SUGGESTED MOVES (pick an index for `move`, or -1)",
    "pending_check": "CHECK QUESTION PENDING (the learner may be answering it)",
    "exercises": "EXERCISES AVAILABLE FOR THIS CONCEPT",
    "task": "EXERCISE THE LEARNER IS WORKING ON",
    "thread": "RECENT CONVERSATION",
}


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


@dataclass
class TutorContext:
    sections: dict[str, str] = field(default_factory=dict)
    concept_ids: set[str] = field(default_factory=set)  # ids the tutor may cite as evidence
    offered: list[Move] = field(default_factory=list)

    @property
    def sizes(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.sections.items()}

    @property
    def text(self) -> str:
        parts = [f"## {_TITLES[k]}\n{self.sections[k]}" for k in _ORDER if self.sections.get(k)]
        return "\n\n".join(parts)


def _line(c: Concept, status: BeliefStatus, extra: str = "") -> str:
    summary = clip(c.card.one_line, 110)
    return f"- {c.id} | {c.name}{extra} | {status.value}: {summary}"


@dataclass
class ContextBuilder:
    budget: ContextSettings

    def build(
        self,
        *,
        graph: KnowledgeGraph,
        state: LearnerState,
        focus: str | None,
        offered: list[Move],
        thread: list[Turn],
        notes: LearnerNotes,
        pending: PendingCheck | None,
        exercises: list[str] | None = None,
        task_text: str = "",
        visible: set[str] | None = None,
    ) -> TutorContext:
        b = self.budget
        ctx = TutorContext(offered=offered)
        names = {c.id: c for c in graph.concepts() if visible is None or c.id in visible}
        focus_c = names.get(focus) if focus else None

        if focus_c is not None:
            ctx.sections["focus"] = clip(self._focus(focus_c, state), b.focus)
            ctx.concept_ids.add(focus_c.id)
            pre, nxt, bridge = self._neighbours(graph, focus_c.id, names, state)
            ctx.sections["prerequisites"] = clip("\n".join(pre), b.prerequisites)
            ctx.sections["leads_to"] = clip("\n".join(nxt), b.leads_to)
            ctx.sections["bridges"] = clip("\n".join(bridge), b.bridges)
            for lines in (pre, nxt, bridge):
                ctx.concept_ids.update(line.split(" | ")[0].lstrip("- ") for line in lines)
            ctx.sections["outline"] = clip(self._outline(graph, state, names), b.outline)
        else:
            ctx.sections["map"] = clip(self._map(graph, state, names), b.map)

        ctx.sections["learner"] = clip(self._learner(focus_c, state, notes, names), b.learner)
        ctx.sections["moves"] = clip(self._moves(offered), b.moves)
        if pending is not None:
            ctx.sections["pending_check"] = clip(self._pending(pending, names), b.pending_check)
            ctx.concept_ids.add(pending.concept_id)
        if exercises:
            ctx.sections["exercises"] = clip("\n".join(f"- {t}" for t in exercises), b.exercises)
        if task_text:
            ctx.sections["task"] = clip(task_text, b.task)
        ctx.sections["thread"] = self._thread(thread, b.thread, b.turns)
        return ctx

    # ---------------------------------------------------------------- sections

    def _focus(self, c: Concept, state: LearnerState) -> str:
        st = state.concept(c.id)
        lines = [f"{c.id} | {c.name} [{c.kind.value}] | learner status: {st.status.value}"]
        if c.card.one_line:
            lines.append(f"Summary: {c.card.one_line}")
        if c.card.definition:
            lines.append(f"Definition: {c.card.definition}")
        for k in c.card.key_ideas:
            lines.append(f"{k.id}: {k.text}")
        for ex in c.card.examples[:2]:
            lines.append(f"Example: {ex}")
        return "\n".join(lines)

    def _neighbours(
        self, graph: KnowledgeGraph, cid: str, names: dict[str, Concept], state: LearnerState
    ) -> tuple[list[str], list[str], list[str]]:
        pre: list[str] = []
        nxt: list[str] = []
        bridge: list[str] = []
        for r in graph.relations_of(cid):
            other = r.target if r.source == cid else r.source
            c = names.get(other)
            if c is None:
                continue
            st = state.concept(other).status
            if r.type is RelationType.PREREQUISITE_OF:
                if r.target == cid:
                    nec = r.necessity.value if r.necessity else "?"
                    pre.append(_line(c, st, f" ({nec})"))
                else:
                    nxt.append(_line(c, st))
            else:
                bridge.append(_line(c, st, f" ({r.type.value.replace('_', ' ')})"))
        return pre, nxt, bridge

    def _outline(
        self, graph: KnowledgeGraph, state: LearnerState, names: dict[str, Concept]
    ) -> str:
        areas: dict[str, list[str]] = {}
        for c in names.values():
            areas.setdefault(c.topic_ids[0] if c.topic_ids else "other", []).append(c.id)
        rows = []
        for area, ids in areas.items():
            done = sum(state.concept(i).status.understood for i in ids)
            rows.append(f"- {area}: {done}/{len(ids)}")
        return "\n".join(rows)

    def _map(self, graph: KnowledgeGraph, state: LearnerState, names: dict[str, Concept]) -> str:
        areas: dict[str, list[Concept]] = {}
        for c in graph.concepts():
            if c.id in names:
                areas.setdefault(c.topic_ids[0] if c.topic_ids else "other", []).append(c)
        rows = []
        for area, concepts in areas.items():
            items = "; ".join(f"[{_MARK[state.concept(c.id).status]}]{c.name}" for c in concepts)
            done = sum(state.concept(c.id).status.understood for c in concepts)
            rows.append(f"{area} ({done}/{len(concepts)}): {items}")
        return "\n".join(rows)

    def _learner(
        self,
        focus: Concept | None,
        state: LearnerState,
        notes: LearnerNotes,
        names: dict[str, Concept],
    ) -> str:
        lines: list[str] = []
        if focus is not None:
            st = state.concept(focus.id)
            facets = ", ".join(f"{f}: {b.status.value}" for f, b in st.facets.items())
            lines.append(f"Focus beliefs - {facets}")
            lines.append(
                f"visited: {'yes' if st.visited else 'no'}; "
                f"learner's own flag: {st.self_flag.value if st.self_flag else 'none'}"
            )
        weak = [
            names[i].name
            for i, s in state.concepts.items()
            if i in names and s.status in (BeliefStatus.LIKELY_NOT, BeliefStatus.UNCERTAIN)
        ][:8]
        if weak:
            lines.append("Struggling or shaky: " + "; ".join(weak))
        done = sum(1 for i, s in state.concepts.items() if i in names and s.status.understood)
        lines.append(f"Understood so far: {done}/{len(names)} concepts")
        for n in notes.entries[-5:]:
            lines.append(f"Note: {n.text}")
        return "\n".join(lines)

    def _moves(self, offered: list[Move]) -> str:
        if not offered:
            return "(none)"
        rows = []
        for i, m in enumerate(offered):
            target = m.concept_name or m.task_id or ""
            rows.append(f"{i}: {m.kind} {target} - {m.reason}")
        return "\n".join(rows)

    def _pending(self, pending: PendingCheck, names: dict[str, Concept]) -> str:
        name = names[pending.concept_id].name if pending.concept_id in names else pending.concept_id
        expected = "; ".join(pending.expected) or "(not specified)"
        return (
            f"Question asked: {pending.question}\n"
            f"About: {pending.concept_id} ({name}), facet {pending.facet.value}\n"
            f"A good answer would show: {expected}"
        )

    def _thread(self, turns: list[Turn], limit: int, max_turns: int) -> str:
        recent = turns[-max_turns:]
        lines = [f"{'Learner' if t.role == 'learner' else 'Tutor'}: {t.text}" for t in recent]
        while len(lines) > 1 and sum(len(x) + 1 for x in lines) > limit:
            lines.pop(0)  # drop the oldest turn first
        if lines and sum(len(x) + 1 for x in lines) > limit:
            lines = [clip(lines[-1], limit)]
        return "\n".join(lines) if lines else "(start of conversation)"
