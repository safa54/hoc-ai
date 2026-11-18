from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from hoc.core.changes import AddConcept, AddRelation, ChangeSet, GraphOp
from hoc.core.common import Actor
from hoc.core.knowledge import Concept, Necessity, Relation, RelationType
from hoc.store import ChangeSetStore, Database, KnowledgeGraph

AGENT = Actor(kind="agent", name="test-agent", version="1")
EXPERT = Actor(kind="human", name="expert")


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def store(db: Database) -> ChangeSetStore:
    return ChangeSetStore(db)


def cs(*ops: GraphOp, author: Actor = AGENT, rationale: str = "test") -> ChangeSet:
    return ChangeSet(author=author, rationale=rationale, ops=list(ops))


def concept(id_: str, name: str | None = None) -> AddConcept:
    return AddConcept(concept=Concept(id=id_, name=name or id_.replace("-", " ")))


def prereq(a: str, b: str, necessity: Necessity = Necessity.ESSENTIAL) -> AddRelation:
    return AddRelation(
        relation=Relation(
            type=RelationType.PREREQUISITE_OF, source=a, target=b, necessity=necessity
        )
    )


def apply(store: ChangeSetStore, changeset: ChangeSet) -> ChangeSet:
    store.propose(changeset)
    return store.apply(changeset.id)


def seed_basic(store: ChangeSetStore) -> None:
    """a -> b -> c prerequisite chain."""
    apply(store, cs(concept("a"), concept("b"), concept("c"), prereq("a", "b"), prereq("b", "c")))


def graph(db: Database) -> KnowledgeGraph:
    return KnowledgeGraph.load(db)


# ----------------------------------------------------------- agent-team helpers

DEMO_FIXTURE = str(
    Path(__file__).resolve().parents[1] / "data" / "examples" / "gcd_lcm_fixture.json"
)


def demo_settings(**agent_overrides):
    """Settings whose every model call is replayed from the recorded GCD/LCM fixture."""
    from hoc.config import load_settings, with_overrides

    agents = {"max_rounds": 1, "min_tests": 4, "max_refine_steps": 2, **agent_overrides}
    return with_overrides(
        load_settings(),
        {
            "llm": {
                "default_provider": "demo",
                "models": {"fast": "demo:x", "standard": "demo:x", "strong": "demo:x"},
                "providers": {
                    "demo": {"kind": "fixture", "path": DEMO_FIXTURE, "extra": {"delay": 0}}
                },
            },
            "agents": agents,
        },
    )


@pytest.fixture
def workspace(tmp_path):
    from hoc.core.build import Brief
    from hoc.services import TopicWorkspace

    ws = TopicWorkspace.open(str(tmp_path / "ws.db"), demo_settings())
    ws.set_brief(Brief(title="GCD and LCM", audience="Grades 6-10", description="gcd, lcm"))
    yield ws
    ws.close()
