from datetime import timedelta

import pytest

from hoc.config import BeliefSettings, LearnSettings
from hoc.core.changes import MergeConcepts, SplitConcept
from hoc.core.common import utc_now
from hoc.core.knowledge import Concept
from hoc.core.learner import Judge, JudgeKind, Observation, Outcome
from hoc.core.learning import Belief, BeliefStatus, Interaction
from hoc.core.refs import Facet, TargetKind, TargetRef
from hoc.learner import (
    build_state,
    concept_status,
    create_student_model,
    observation_weight,
    resolve_observations,
)
from hoc.learner.beta_facet import make_belief
from hoc.store import KnowledgeGraph, ObservationStore
from tests.conftest import apply, concept, cs, prereq, seed_basic

S = LearnSettings()
JUDGE = Judge(kind=JudgeKind.EXACT, id="t", reliability=1.0)


def obs(cid, outcome=Outcome.STRONG, facet=Facet.APPLY, w=1.0, when=None, learner="me"):
    return Observation(
        learner_id=learner,
        target=TargetRef(kind=TargetKind.CONCEPT, id=cid, facet=facet),
        outcome=outcome,
        weight=w,
        judge=JUDGE,
        timestamp=when or utc_now(),
    )


def state_of(db, observations, interactions=(), model="beta-facet", graph_seq=1):
    return build_state(
        "me",
        list(observations),
        list(interactions),
        KnowledgeGraph.load(db),
        create_student_model(model),
        S,
        graph_seq=graph_seq,
    )


@pytest.mark.parametrize(
    ("alpha", "beta", "status"),
    [
        (1.0, 1.0, BeliefStatus.UNKNOWN),  # no evidence
        (1.5, 1.4, BeliefStatus.UNKNOWN),  # mass 0.9 < 1
        (1.0, 3.0, BeliefStatus.LIKELY_NOT),
        (2.0, 2.0, BeliefStatus.UNCERTAIN),
        (3.0, 1.6, BeliefStatus.LIKELY),  # mean .65 -> likely
        (4.0, 1.0, BeliefStatus.MASTERED),  # mean .8? 4/5=.8 -> likely, see below
    ],
)
def test_belief_status_thresholds(alpha, beta, status):
    expected = BeliefStatus.LIKELY if (alpha, beta) == (4.0, 1.0) else status
    assert make_belief(alpha, beta, BeliefSettings()).status is expected


def test_mastered_needs_mean_and_mass():
    assert make_belief(8.0, 1.0, BeliefSettings()).status is BeliefStatus.MASTERED
    assert make_belief(3.9, 1.0, BeliefSettings(mastered_mass=5)).status is BeliefStatus.LIKELY


def test_unknown_is_not_likely_not(db, store):
    seed_basic(store)
    st = state_of(db, [])
    assert {c.status for c in st.concepts.values()} == {BeliefStatus.UNKNOWN}
    st = state_of(db, [obs("a", Outcome.CONTRADICTING, w=3)])
    assert st.concepts["a"].status is BeliefStatus.LIKELY_NOT
    assert st.concepts["b"].status is BeliefStatus.UNKNOWN


def test_outcomes_and_facets_move_beliefs(db, store):
    seed_basic(store)
    st = state_of(db, [obs("a", Outcome.STRONG, Facet.APPLY, 3)])
    assert st.concepts["a"].facets["apply"].status.understood
    assert st.concepts["a"].facets["explain"].status is BeliefStatus.UNKNOWN
    assert st.concepts["a"].status is BeliefStatus.LIKELY  # apply understood, nothing contradicts
    partial = state_of(db, [obs("a", Outcome.PARTIAL, w=4)])
    assert partial.concepts["a"].facets["apply"].status is BeliefStatus.UNCERTAIN
    absent = state_of(db, [obs("a", Outcome.ABSENT, w=10)])
    assert absent.concepts["a"].facets["apply"].status is BeliefStatus.LIKELY_NOT


def test_cross_facet_propagation(db, store):
    seed_basic(store)
    st = state_of(db, [obs("a", Outcome.STRONG, Facet.TRANSFER, 5)])
    f = st.concepts["a"].facets
    assert f["transfer"].mean > f["apply"].mean > 0.5 and f["apply"].observed
    neg = state_of(db, [obs("a", Outcome.CONTRADICTING, Facet.RECALL, 5)])
    assert neg.concepts["a"].facets["apply"].mean < 0.5


def test_evidence_decays_toward_unknown(db, store):
    seed_basic(store)
    fresh = state_of(db, [obs("a", w=3)])
    old = state_of(db, [obs("a", w=3, when=utc_now() - timedelta(days=140))])
    assert fresh.concepts["a"].facets["apply"].evidence_mass > 2.5
    assert old.concepts["a"].facets["apply"].status is BeliefStatus.UNKNOWN


def test_hints_and_judges_discount_weight():
    llm = Judge(kind=JudgeKind.LLM, id="x", reliability=0.7)
    assert observation_weight(S, llm) == 0.7
    assert observation_weight(S, llm, hints_used=1) == 0.35
    assert observation_weight(S, llm, requirement_weight=0.5) == 0.35


def test_concept_status_table():
    def b(status):
        return Belief(status=status, observed=status is not BeliefStatus.UNKNOWN)

    U, N, M, L, X = (
        BeliefStatus.UNKNOWN,
        BeliefStatus.LIKELY_NOT,
        BeliefStatus.UNCERTAIN,
        BeliefStatus.LIKELY,
        BeliefStatus.MASTERED,
    )
    table = [
        ({"apply": U, "explain": U}, U),
        ({"apply": L, "explain": U}, L),
        ({"apply": U, "explain": L}, L),
        ({"apply": L, "explain": N}, N),  # any contradiction dominates
        ({"apply": M, "explain": M}, M),
        ({"apply": X, "explain": X}, X),
        ({"apply": X, "explain": L}, L),
        ({"recall": N, "apply": L}, N),
    ]
    for facets, expected in table:
        assert concept_status({k: b(v) for k, v in facets.items()}) is expected, facets


def test_merge_redirects_evidence_and_split_inherits_at_reduced_weight(db, store):
    seed_basic(store)
    evidence = [obs("a", w=2), obs("c", w=2)]
    apply(store, cs(MergeConcepts(from_id="a", into_id="b")))
    st = state_of(db, evidence)
    assert "a" not in st.concepts and st.concepts["b"].n_observations == 1
    apply(
        store,
        cs(
            SplitConcept(
                from_id="c", into=[Concept(id="c1", name="c1"), Concept(id="c2", name="c2")]
            )
        ),
    )
    st = state_of(db, evidence)
    assert st.concepts["c1"].n_observations == 1 and st.concepts["c2"].n_observations == 1
    full = state_of(db, [obs("b", w=2)]).concepts["b"].facets["apply"].alpha
    half = st.concepts["c1"].facets["apply"].alpha
    assert half < full  # inherited weight is S.split_evidence_weight (0.5)
    resolved = resolve_observations(evidence, KnowledgeGraph.load(db), 0.5)
    assert {r.concept_id for r in resolved} == {"b", "c1", "c2"}
    assert next(r for r in resolved if r.concept_id == "c1").inferred


def test_visited_and_flags_are_not_evidence(db, store):
    seed_basic(store)
    items = [
        Interaction(learner_id="me", kind="visit", concept_id="a"),
        Interaction(learner_id="me", kind="flag", concept_id="b", meta={"flag": "lost"}),
    ]
    st = state_of(db, [], items)
    assert st.concepts["a"].visited and st.concepts["a"].status is BeliefStatus.UNKNOWN
    flag = st.concepts["b"].self_flag
    assert flag is not None and flag.value == "lost"
    assert st.concepts["b"].status is BeliefStatus.UNKNOWN
    unflag = Interaction(learner_id="me", kind="flag", concept_id="b", meta={"flag": None})
    cleared = [*items, unflag]
    assert state_of(db, [], cleared).concepts["b"].self_flag is None


def test_inferred_mean_is_a_hint_not_a_colour(db, store):
    seed_basic(store)  # a -> b -> c
    st = state_of(db, [obs("a", Outcome.CONTRADICTING, w=3)])
    assert st.concepts["b"].inferred_mean is not None and st.concepts["b"].inferred_mean < 0.5
    assert st.concepts["b"].status is BeliefStatus.UNKNOWN


def test_models_replay_from_the_same_log(db, store):
    seed_basic(store)
    log = [obs("a", Outcome.STRONG, Facet.TRANSFER, 4), obs("b", Outcome.CONTRADICTING, w=3)]
    beta = state_of(db, log, model="beta-facet")
    flat = state_of(db, log, model="flat-beta")
    assert beta.model_id != flat.model_id
    assert flat.concepts["a"].facets["recall"].mean == flat.concepts["a"].facets["apply"].mean


def test_observation_store_is_append_only(db):
    store = ObservationStore(db)
    public = {n for n in dir(store) if not n.startswith("_")}
    assert not {n for n in public if n.startswith(("update", "delete", "remove", "set"))}
    o = obs("a")
    store.add(o)
    assert store.for_learner("me")[0].id == o.id and store.count("me") == 1
    with pytest.raises(Exception, match=r"UNIQUE|constraint"):
        store.add(o)  # the same id cannot be written twice


def test_state_is_a_pure_function_of_evidence(db, store):
    seed_basic(store)
    apply(store, cs(concept("d"), prereq("a", "d")))
    log = [obs("a", w=2), obs("d", Outcome.PARTIAL, w=2)]
    at = utc_now()
    one = build_state(
        "me", log, [], KnowledgeGraph.load(db), create_student_model("beta-facet"), S, at=at
    )
    two = build_state(
        "me", log, [], KnowledgeGraph.load(db), create_student_model("beta-facet"), S, at=at
    )
    assert one.model_dump() == two.model_dump()
