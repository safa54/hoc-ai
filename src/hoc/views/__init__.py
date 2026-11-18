"""Graph views / lenses (ADR 0012): connected, possibly overlapping subgraphs to look at.

Presentation only: nothing here changes the graph. Reused by the Studio, the Learn side and
anything else that needs "the part of the graph about X".
"""

from hoc.views.connect import connect, importance, needed_for, neighbourhood, undirected
from hoc.views.providers import (
    VIEW_PROVIDERS,
    ViewInputs,
    around_view,
    compute_views,
    make_view,
    needed_for_view,
    register_view_provider,
)

__all__ = [
    "VIEW_PROVIDERS",
    "ViewInputs",
    "around_view",
    "compute_views",
    "connect",
    "importance",
    "make_view",
    "needed_for",
    "needed_for_view",
    "neighbourhood",
    "register_view_provider",
    "undirected",
]
