"""``beta-facet`` (default) and ``flat-beta`` student models (ARCHITECTURE §8.2, §8.3).

Per (concept, facet) a Beta(alpha, beta) with prior (1, 1). Observation weight ``w`` (already
including judge reliability and hint discount) moves it:
    strong: alpha += w; partial: alpha += w/2, beta += w/2; absent: beta += 0.3w;
    contradicting: beta += w
Evidence decays at read time with the configured half-life, so long-unpractised concepts drift
back toward "unknown" instead of flipping to "not understood".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from hoc.config import BeliefSettings
from hoc.core.learner import Outcome
from hoc.core.learning import Belief, BeliefContribution, BeliefStatus
from hoc.core.refs import CONCEPT_FACETS, Facet
from hoc.learner.base import ResolvedObservation, register_student_model

Delta = tuple[Facet, float, float, bool]  # facet, d_alpha, d_beta, propagated


def decay_factor(obs_time: datetime, at: datetime, half_life_days: float) -> float:
    days = max(0.0, (at - obs_time).total_seconds() / 86400.0)
    return float(2.0 ** (-days / half_life_days)) if half_life_days > 0 else 1.0


def deltas(outcome: Outcome, facet: Facet, w: float, s: BeliefSettings) -> list[Delta]:
    out: list[Delta] = []
    if outcome is Outcome.STRONG:
        out.append((facet, w, 0.0, False))
        if facet is Facet.TRANSFER:
            out += [
                (Facet.APPLY, s.propagate_transfer * w, 0.0, True),
                (Facet.EXPLAIN, s.propagate_transfer * w, 0.0, True),
            ]
        elif facet is Facet.EXPLAIN:
            out.append((Facet.RECALL, s.propagate_explain * w, 0.0, True))
    elif outcome is Outcome.PARTIAL:
        out.append((facet, 0.5 * w, 0.5 * w, False))
    elif outcome is Outcome.ABSENT:
        out.append((facet, 0.0, 0.3 * w, False))
    else:  # contradicting
        out.append((facet, 0.0, w, False))
        if facet is Facet.RECALL:
            out += [
                (Facet.APPLY, 0.0, s.propagate_recall_negative * w, True),
                (Facet.EXPLAIN, 0.0, s.propagate_recall_negative * w, True),
            ]
    return out


def make_belief(alpha: float, beta: float, s: BeliefSettings) -> Belief:
    mean = alpha / (alpha + beta)
    mass = alpha + beta - 2.0
    if mass < s.unknown_mass:
        status = BeliefStatus.UNKNOWN
    elif mean < s.likely_not:
        status = BeliefStatus.LIKELY_NOT
    elif mean < s.uncertain:
        status = BeliefStatus.UNCERTAIN
    elif mean < s.likely:
        status = BeliefStatus.LIKELY
    else:
        status = BeliefStatus.MASTERED if mass >= s.mastered_mass else BeliefStatus.LIKELY
    return Belief(
        mean=round(mean, 4),
        alpha=round(alpha, 4),
        beta=round(beta, 4),
        evidence_mass=round(max(mass, 0.0), 4),
        status=status,
        observed=mass > 0.0,
    )


@register_student_model("beta-facet")
class BetaFacet:
    id = "beta-facet"

    def _facet(self, r: ResolvedObservation) -> Facet:
        return r.observation.target.facet or Facet.APPLY

    def _contribs(
        self, r: ResolvedObservation, at: datetime, s: BeliefSettings
    ) -> tuple[float, float, list[Delta]]:
        f = decay_factor(r.observation.timestamp, at, s.half_life_days)
        w = r.observation.weight * r.multiplier * f
        return w, f, deltas(r.observation.outcome, self._facet(r), w, s)

    def compute(
        self, observations: list[ResolvedObservation], at: datetime, settings: BeliefSettings
    ) -> dict[str, dict[Facet, Belief]]:
        acc: dict[str, dict[Facet, list[float]]] = defaultdict(
            lambda: {f: [1.0, 1.0] for f in CONCEPT_FACETS}
        )
        for r in observations:
            _, _, ds = self._contribs(r, at, settings)
            for facet, da, db, _ in ds:
                if facet in acc[r.concept_id]:
                    acc[r.concept_id][facet][0] += da
                    acc[r.concept_id][facet][1] += db
        return {
            cid: {f: make_belief(a, b, settings) for f, (a, b) in facets.items()}
            for cid, facets in acc.items()
        }

    def explain(
        self,
        observations: list[ResolvedObservation],
        concept_id: str,
        facet: Facet | None,
        at: datetime,
        settings: BeliefSettings,
    ) -> list[BeliefContribution]:
        out: list[BeliefContribution] = []
        for r in observations:
            if r.concept_id != concept_id:
                continue
            w, f, ds = self._contribs(r, at, settings)
            for fc, da, db, propagated in ds:
                if facet is not None and fc is not facet:
                    continue
                o = r.observation
                out.append(
                    BeliefContribution(
                        observation_id=o.id,
                        outcome=o.outcome.value,
                        facet=fc,
                        delta_alpha=round(da, 4),
                        delta_beta=round(db, 4),
                        weight=round(w, 4),
                        decay=round(f, 4),
                        quote=o.quote,
                        rationale=o.rationale,
                        judge=f"{o.judge.kind.value}:{o.judge.id}",
                        timestamp=o.timestamp,
                        inferred=propagated or r.inferred,
                    )
                )
        return out


@register_student_model("flat-beta")
class FlatBeta(BetaFacet):
    """One Beta per concept, facets ignored: the ablation that shows whether facets matter.
    The single belief is reported under every facet so the interface needs no special case."""

    id = "flat-beta"

    def _facet(self, r: ResolvedObservation) -> Facet:
        return Facet.APPLY

    def compute(
        self, observations: list[ResolvedObservation], at: datetime, settings: BeliefSettings
    ) -> dict[str, dict[Facet, Belief]]:
        flat = super().compute(observations, at, settings)
        out: dict[str, dict[Facet, Belief]] = {}
        for cid, facets in flat.items():
            belief = facets[Facet.APPLY]
            out[cid] = {f: belief for f in CONCEPT_FACETS}
        return out
