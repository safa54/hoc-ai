import pytest
from pydantic import ValidationError

from hoc.core.common import slugify
from hoc.core.knowledge import Concept, Necessity, Relation, RelationType


def test_slugify():
    assert slugify("Bayes' Rule (2nd form)") == "bayes-rule-2nd-form"
    with pytest.raises(ValueError):
        slugify("???")


def test_concept_id_must_be_slug():
    with pytest.raises(ValidationError):
        Concept(id="Not A Slug", name="x")


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        Concept(id="x", name="x", colour="red")  # type: ignore[call-arg]


def test_prerequisite_requires_necessity():
    with pytest.raises(ValidationError):
        Relation(type=RelationType.PREREQUISITE_OF, source="a", target="b")


def test_non_prerequisite_rejects_necessity():
    with pytest.raises(ValidationError):
        Relation(type=RelationType.RELATED, source="a", target="b", necessity=Necessity.ESSENTIAL)


def test_self_loop_rejected():
    with pytest.raises(ValidationError):
        Relation(type=RelationType.RELATED, source="a", target="a")


def test_symmetric_relations_are_canonical_and_have_natural_id():
    r1 = Relation(type=RelationType.CONTRASTS_WITH, source="b", target="a")
    r2 = Relation(type=RelationType.CONTRASTS_WITH, source="a", target="b")
    assert r1.id == r2.id == "contrasts_with:a->b"


def test_directed_relation_id_keeps_direction():
    r = Relation(
        type=RelationType.PREREQUISITE_OF, source="b", target="a", necessity=Necessity.SUPPORTIVE
    )
    assert r.id == "prerequisite_of:b->a"
