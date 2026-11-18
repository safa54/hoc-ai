"""Policies: ``heuristic`` (default) and ``prereq-order`` (baseline).

Candidates and their score components are plain numbers from beliefs and graph structure, so a
researcher can read exactly why a move was offered (and change ``learn.policy_weights``).
"""

from __future__ import annotations

from hoc.core.knowledge import RelationType
from hoc.core.learning import BeliefStatus, Move
from hoc.core.refs import Facet
from hoc.policy.base import PolicyContext, register_policy
from hoc.policy.frontier import dependents, essential_prerequisites, frontier, weakness
from hoc.policy.path import learning_path

_BRIDGE_TYPES = {
    RelationType.ANALOGOUS_TO,
    RelationType.CONTRASTS_WITH,
    RelationType.SPECIALIZES,
}
_STRUGGLING = (BeliefStatus.UNKNOWN, BeliefStatus.UNCERTAIN, BeliefStatus.LIKELY_NOT)


def _name(ctx: PolicyContext, cid: str) -> str:
    try:
        return ctx.graph.concept(cid).name
    except Exception:
        return cid


def _strength(status: BeliefStatus) -> float:
    return {BeliefStatus.MASTERED: 1.0, BeliefStatus.LIKELY: 0.8}.get(status, 0.0)


def _recent_failure(ctx: PolicyContext, cid: str) -> float:
    st = ctx.state.concept(cid)
    observed_bad = any(
        b.observed and b.status in (BeliefStatus.LIKELY_NOT, BeliefStatus.UNCERTAIN)
        for b in st.facets.values()
    )
    return (
        1.0 if observed_bad or (st.self_flag is not None and st.self_flag.value == "lost") else 0.0
    )


def _finish(ctx: PolicyContext, moves: list[Move]) -> list[Move]:
    w = ctx.settings.policy_weights
    for m in moves:
        if m.key in ctx.recent_moves:  # do not keep suggesting what was just ignored
            m.components["repetition"] = -w.get("repetition", 0.5)
            m.score += m.components["repetition"]
    moves.sort(key=lambda m: -m.score)
    moves.append(Move(kind="stay", reason="Answer here without moving", score=0.05))
    return moves


@register_policy("heuristic")
class HeuristicPolicy:
    id = "heuristic"

    def candidates(self, ctx: PolicyContext) -> list[Move]:
        w = ctx.settings.policy_weights
        moves: list[Move] = []
        focus = ctx.focus
        front = frontier(ctx.graph, ctx.state, ctx.settings)

        if focus is not None and focus in ctx.state.concepts:
            fs = ctx.state.concept(focus)
            fail = _recent_failure(ctx, focus)
            if fs.status is not BeliefStatus.MASTERED:
                for p in essential_prerequisites(ctx.graph, focus):
                    ps = ctx.state.concept(p).status
                    if weakness(ps) < 0.5:
                        continue
                    comp = {
                        "weakness": w["weakness"] * weakness(ps),
                        "necessity": w["necessity"],
                        "recent_failure": w["recent_failure"] * fail,
                    }
                    moves.append(
                        Move(
                            kind="review_prerequisite",
                            concept_id=p,
                            concept_name=_name(ctx, p),
                            reason=f"An essential prerequisite is {ps.value.replace('_', ' ')}",
                            score=sum(comp.values()),
                            components=comp,
                        )
                    )
            if fs.status in _STRUGGLING:
                for r in ctx.graph.relations_of(focus):
                    if r.type not in _BRIDGE_TYPES:
                        continue
                    other = r.target if r.source == focus else r.source
                    strength = _strength(ctx.state.concept(other).status)
                    if strength == 0.0:
                        continue
                    comp = {
                        "readiness": w["readiness"] * strength,
                        "recent_failure": 0.5 * w["recent_failure"] * fail,
                    }
                    moves.append(
                        Move(
                            kind="use_analogy",
                            concept_id=other,
                            concept_name=_name(ctx, other),
                            reason=f"You already know this ({r.type.value.replace('_', ' ')})",
                            score=sum(comp.values()),
                            components=comp,
                        )
                    )
            facet, comp = self._check_target(ctx, focus)
            if facet is not None:
                moves.append(
                    Move(
                        kind="check",
                        concept_id=focus,
                        concept_name=_name(ctx, focus),
                        facet=facet,
                        reason=f"Little evidence about '{facet.value}' yet",
                        score=sum(comp.values()),
                        components=comp,
                    )
                )
            if ctx.bank is not None:
                tasks = ctx.bank.for_concept(focus, exclude_ids=ctx.seen_task_ids, limit=1)
                if tasks:
                    apply_b = fs.facets.get("apply")
                    unc = 1.0 - min(1.0, (apply_b.evidence_mass if apply_b else 0.0) / 3.0)
                    comp = {"info_gain": w["info_gain"] * unc, "base": 0.3}
                    moves.append(
                        Move(
                            kind="practice",
                            concept_id=focus,
                            concept_name=_name(ctx, focus),
                            task_id=tasks[0].id,
                            facet=Facet.APPLY,
                            reason=f"Exercise available: {tasks[0].title or 'practice'}",
                            score=sum(comp.values()),
                            components=comp,
                        )
                    )

        area = ctx.graph.concept(focus).topic_ids[:1] if focus in ctx.state.concepts else []
        deps = set(dependents(ctx.graph, focus)) if focus else set()
        # the learner's path: the next steps on it come first, so "advance" follows one
        # coherent route (towards the goal, if there is one) instead of any ready concept
        ready_set = set(front)
        ahead = [
            c
            for c in learning_path(ctx.graph, goal=ctx.goal, area_order=ctx.area_order)
            if c in ready_set and c != focus
        ]
        step = {c: i for i, c in enumerate(ahead)}
        advances: list[Move] = []
        for n in front:
            if n == focus:
                continue
            status = ctx.state.concept(n).status
            if n in deps:
                prox = 1.0
            elif area and ctx.graph.concept(n).topic_ids[:1] == area:
                prox = 0.5
            else:
                prox = 0.0
            ready = 1.0 if status is BeliefStatus.UNKNOWN else 1.0 - 0.5 * weakness(status)
            comp = {"readiness": w["readiness"] * ready, "proximity": w["proximity"] * prox}
            if n in step:
                comp["path"] = w.get("path", 1.0) / (1 + step[n])
            if n in step and step[n] == 0:
                reason = "Next step towards your goal" if ctx.goal else "Next step on your path"
            else:
                reason = "Its prerequisites are in place"
            advances.append(
                Move(
                    kind="advance",
                    concept_id=n,
                    concept_name=_name(ctx, n),
                    reason=reason,
                    score=sum(comp.values()) - 0.001 * len(advances),  # stable learning order
                    components=comp,
                )
            )
        # one clear next step: every further "advance" is an alternative and ranks lower, so
        # checks and exercises on the current concept are not crowded out
        advances.sort(key=lambda m: -m.score)
        for k, m in enumerate(advances[1:], 1):
            m.components["alternative"] = -w.get("alternative", 0.4) * min(k, 3)
            m.score += m.components["alternative"]
        moves += advances
        return _finish(ctx, moves)

    def _check_target(
        self, ctx: PolicyContext, focus: str
    ) -> tuple[Facet | None, dict[str, float]]:
        w = ctx.settings.policy_weights
        st = ctx.state.concept(focus)
        apply_b, explain_b = st.facets.get("apply"), st.facets.get("explain")
        superficial = (
            apply_b is not None
            and apply_b.status.understood
            and (explain_b is None or explain_b.status is BeliefStatus.UNKNOWN)
        )
        if superficial:
            return Facet.EXPLAIN, {"depth_gap": w["depth_gap"], "info_gain": 0.5 * w["info_gain"]}
        for facet in (Facet.APPLY, Facet.EXPLAIN, Facet.RECALL):
            b = st.facets.get(facet.value)
            if b is not None and b.status is BeliefStatus.UNKNOWN:
                return facet, {"info_gain": w["info_gain"]}
        return None, {}


@register_policy("prereq-order")
class PrerequisiteOrder:
    """Baseline: follow the topological order; advance to the first concept not yet understood."""

    id = "prereq-order"

    def candidates(self, ctx: PolicyContext) -> list[Move]:
        moves: list[Move] = []
        for i, cid in enumerate(ctx.graph.learning_order()):
            if ctx.state.concept(cid).status.understood or cid == ctx.focus:
                continue
            moves.append(
                Move(
                    kind="advance",
                    concept_id=cid,
                    concept_name=_name(ctx, cid),
                    reason="Next in prerequisite order",
                    score=1.0 - 0.001 * i,
                )
            )
            if len(moves) >= 3:
                break
        return _finish(ctx, moves)
