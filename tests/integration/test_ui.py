from pathlib import Path

from streamlit.testing.v1 import AppTest

from hoc.cli import EXAMPLE_SEED
from hoc.core.common import Actor
from hoc.services import HocApp
from hoc.services.render import graph_layout, graph_to_dot

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def page(name: str) -> str:
    return str(APP_DIR / name)


def seeded(tmp_path):
    app = HocApp.open(db_path=tmp_path / "ui.db")
    cs = app.changesets.propose(app.parse_changeset(EXAMPLE_SEED.read_text()))
    app.changesets.approve(cs.id, Actor(kind="human", name="t"))
    return app, cs


def test_dot_rendering(tmp_path):
    app, _ = seeded(tmp_path)
    dot = graph_to_dot(app.graph())
    assert dot.startswith("digraph") and '"bayes-rule"' in dot and "penwidth=3" in dot
    assert '"bayes-rule" ->' not in graph_to_dot(app.graph(), [])


def test_layout_exposes_stable_ids_for_clicks(tmp_path):
    app, _ = seeded(tmp_path)
    graph = app.graph()
    layout = graph_layout(graph)
    assert {n.id for n in layout.nodes} == {c.id for c in graph.concepts()}
    assert {e.id for e in layout.edges} == {r.id for r in graph.relations()}
    assert graph_layout(graph, []).edges == []


def test_pages_render_without_exceptions_and_revert_works(tmp_path):
    seeded(tmp_path)
    db = str(tmp_path / "ui.db")
    for name in ("Home.py", "pages/1_Graph_Explorer.py", "pages/2_Change_History.py"):
        at = AppTest.from_file(page(name), default_timeout=30)
        at.session_state["db_path"] = db
        at.run()
        assert not at.exception, (name, at.exception)

    at = AppTest.from_file(page("pages/2_Change_History.py"), default_timeout=30)
    at.session_state["db_path"] = db
    at.run()
    revert = next(b for b in at.button if b.label == "Revert" and not b.proto.disabled)
    revert.click().run()
    assert not at.exception
    assert HocApp.open(db_path=db).graph().concepts() == []
