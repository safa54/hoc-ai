from hoc.store import SnapshotStore
from tests.conftest import apply, concept, cs


def test_snapshot_is_frozen_copy(db, store):
    apply(store, cs(concept("a")))
    snaps = SnapshotStore(db)
    info = snaps.create("v1")
    apply(store, cs(concept("b")))
    assert [c.id for c in snaps.load(info.id).concepts()] == ["a"]
    assert info.applied_seq == 1
    assert [s.label for s in snaps.list()] == ["v1"]
