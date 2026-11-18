"""Shared UI helpers. Streamlit pages import this as a top-level module."""

from __future__ import annotations

import os

import plotly.graph_objects as go
import streamlit as st

from hoc.services import HocApp
from hoc.services.render import GraphLayout


@st.cache_resource
def get_app(db_path: str) -> HocApp:
    return HocApp.open(db_path=db_path)


def app_from_sidebar() -> HocApp:
    default = os.environ.get("HOC_DB", "data/hoc.db")
    db_path = st.sidebar.text_input("Database", value=default, key="db_path")
    return get_app(db_path)


_EDGE_COLOR = {
    "prerequisite_of": "#374151",
    "specializes": "#2563eb",
    "part_of": "#7c3aed",
    "contrasts_with": "#dc2626",
    "analogous_to": "#059669",
    "related": "#9ca3af",
}


def graph_figure(layout: GraphLayout, selected: str | None = None) -> go.Figure:
    """Plotly view of a GraphLayout. customdata carries concept/relation ids for click events.

    Trace 0 = edge lines (decorative), trace 1 = edge midpoints (clickable), trace 2 = nodes.
    """
    fig = go.Figure()
    xs: list[float | None] = []
    ys: list[float | None] = []
    for e in layout.edges:
        xs += [e.x0, e.x1, None]
        ys += [e.y0, e.y1, None]
    fig.add_trace(
        go.Scatter(
            x=xs, y=ys, mode="lines", line={"width": 1.5, "color": "#9ca3af"}, hoverinfo="skip"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[(e.x0 + e.x1) / 2 for e in layout.edges],
            y=[(e.y0 + e.y1) / 2 for e in layout.edges],
            mode="markers",
            customdata=[e.id for e in layout.edges],
            text=[f"{e.source} → {e.type.value} → {e.target}" for e in layout.edges],
            hoverinfo="text",
            marker={
                "size": 14,
                "symbol": "diamond",
                "color": [_EDGE_COLOR[e.type.value] for e in layout.edges],
                "line": {
                    "width": [3 if e.id == selected else 0 for e in layout.edges],
                    "color": "#f59e0b",
                },
            },
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[n.x for n in layout.nodes],
            y=[n.y for n in layout.nodes],
            mode="markers+text",
            text=[n.label for n in layout.nodes],
            textposition="top center",
            customdata=[n.id for n in layout.nodes],
            hoverinfo="text",
            marker={
                "size": 26,
                "color": ["#93c5fd" if n.accepted else "#d1d5db" for n in layout.nodes],
                "line": {
                    "width": [
                        4 if n.id == selected else (3 if n.locked else 1) for n in layout.nodes
                    ],
                    "color": ["#f59e0b" if n.id == selected else "#111827" for n in layout.nodes],
                },
            },
        )
    )
    fig.update_layout(
        showlegend=False,
        height=520,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        xaxis={"visible": False},
        yaxis={"visible": False},
        clickmode="event+select",
        dragmode="pan",
    )
    return fig
