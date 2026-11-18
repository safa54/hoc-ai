import streamlit as st

from hoc.core.changes import ChangeSetStatus
from hoc.core.common import Actor
from hoc.core.errors import HocError
from ui_common import app_from_sidebar

st.set_page_config(page_title="Change History", layout="wide")
st.title("Change History")
app = app_from_sidebar()
expert = Actor(kind="human", name=st.sidebar.text_input("Your name", "expert"))

status_filter = st.selectbox("Status", ["all", *[s.value for s in ChangeSetStatus]])
items = app.changesets.find(None if status_filter == "all" else ChangeSetStatus(status_filter))

for cs in items:
    with st.expander(
        f"{cs.status.value} · {cs.author.name} · {len(cs.ops)} ops · {cs.rationale[:70]}"
    ):
        st.caption(f"{cs.id} · confidence {cs.confidence:.2f} · impact {cs.impact:.2f}")
        st.json([op.model_dump(mode="json") for op in cs.ops], expanded=False)
        cols = st.columns(4)
        actions = {
            "Apply": (ChangeSetStatus.PROPOSED, lambda i=cs.id: app.changesets.apply(i)),
            "Approve": (
                None,
                lambda i=cs.id: app.changesets.approve(i, expert),
            ),
            "Reject": (ChangeSetStatus.PROPOSED, lambda i=cs.id: app.changesets.reject(i, expert)),
            "Revert": (None, lambda i=cs.id: app.changesets.revert(i)),
        }
        allowed = {
            "Apply": cs.status is ChangeSetStatus.PROPOSED,
            "Approve": cs.status in (ChangeSetStatus.PROPOSED, ChangeSetStatus.AUTO_APPLIED),
            "Reject": cs.status is ChangeSetStatus.PROPOSED,
            "Revert": cs.status.is_applied,
        }
        for col, (label, (_, fn)) in zip(cols, actions.items(), strict=True):
            if col.button(label, key=f"{label}-{cs.id}", disabled=not allowed[label]):
                try:
                    fn()
                    st.rerun()
                except HocError as e:
                    st.error(str(e))
