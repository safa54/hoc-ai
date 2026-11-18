import streamlit as st

from hoc.core.knowledge import RelationType
from hoc.services.render import graph_layout
from ui_common import app_from_sidebar, graph_figure

st.set_page_config(page_title="Graph Explorer", layout="wide")
st.title("Graph Explorer")
app = app_from_sidebar()
graph = app.graph()

if not graph.concepts():
    st.info("No concepts yet.")
    st.stop()

types = st.multiselect(
    "Relation types", [t.value for t in RelationType], default=[t.value for t in RelationType]
)
layout = graph_layout(graph, [RelationType(t) for t in types])
concept_ids = {c.id for c in graph.concepts()}
relation_ids = {r.id for r in graph.relations()}

selected: str | None = st.session_state.get("selected_item")
event = st.plotly_chart(
    graph_figure(layout, selected),
    on_select="rerun",
    selection_mode="points",
    key="graph_chart",
    use_container_width=True,
)
points = event.selection.points if event else []
if points:
    raw = points[0].get("customdata")
    clicked = raw[0] if isinstance(raw, list) else raw
    if clicked in concept_ids | relation_ids and clicked != selected:
        st.session_state["selected_item"] = selected = clicked
        st.rerun()
st.caption(
    "Click a circle (concept) or a diamond (relation) · blue = accepted · grey = proposed · "
    "bold border = expert-locked"
)

options = sorted(concept_ids)
index = options.index(selected) if selected in concept_ids else 0
picked = st.selectbox("Concept", options, index=index)
if selected not in relation_ids and picked != selected:
    st.session_state["selected_item"] = selected = picked

if selected in relation_ids:
    r = next(r for r in graph.relations() if r.id == selected)
    st.subheader("Relation")
    nec = f" ({r.necessity.value})" if r.necessity else ""
    st.markdown(f"`{r.source}` →**{r.type.value}**{nec}→ `{r.target}`")
    st.markdown(f"{r.status.value} · confidence {r.confidence:.2f} · v{r.version}")
    st.write(r.rationale or "_no rationale_")
    if st.button("Back to concepts"):
        st.session_state["selected_item"] = r.source
        st.rerun()
else:
    c = graph.concept(selected or picked)
    st.subheader("Concept")
    st.markdown(f"**{c.name}** · `{c.kind.value}` · {c.status.value} · v{c.version}")
    st.write(c.card.one_line or "_no description yet_")
    for k in c.card.key_ideas:
        st.markdown(f"- **{k.id}** {k.text}")
    for r in graph.relations_of(c.id):
        nec = f" ({r.necessity.value})" if r.necessity else ""
        if st.button(f"{r.source} →{r.type.value}{nec}→ {r.target}", key=f"rel-{r.id}"):
            st.session_state["selected_item"] = r.id
            st.rerun()
    for m in graph.misconceptions():
        if c.id in m.concept_ids:
            st.warning(f"Misconception: {m.statement}")
