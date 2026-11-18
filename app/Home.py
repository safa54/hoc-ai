import streamlit as st

from ui_common import app_from_sidebar

st.set_page_config(page_title="hoc-ai", layout="wide")
app = app_from_sidebar()

st.title("hoc-ai")
st.caption("AI-native, graph-based learning system — prototype")

graph = app.graph()
pending = len(app.changesets.find(status=None, limit=1000))
c1, c2, c3 = st.columns(3)
c1.metric("Concepts", len(graph.concepts()))
c2.metric("Relations", len(graph.relations()))
c3.metric("ChangeSets", pending)

if not graph.concepts():
    st.info("The graph is empty. Load the example with `hoc demo seed` (or the button below).")
    if st.button("Load example graph (probability)"):
        from hoc.cli import EXAMPLE_SEED
        from hoc.core.common import Actor

        cs = app.changesets.propose(app.parse_changeset(EXAMPLE_SEED.read_text()))
        app.changesets.approve(cs.id, Actor(kind="human", name="demo"))
        st.rerun()
