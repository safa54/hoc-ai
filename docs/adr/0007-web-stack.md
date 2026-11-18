# ADR 0007 — FastAPI + a build-free SPA for the Studio

**Context.** ARCHITECTURE §12.1 chose Streamlit. The Studio needs live server push, a
graph that updates incrementally with animation, click-to-inspect, pause/step controls and
time travel — Streamlit's rerun model fits none of these.

**Decision.** `hoc.web`: FastAPI JSON API + Server-Sent Events over the persisted event log;
frontend is plain ES modules with Preact+htm and Cytoscape.js (+dagre) vendored under
`web/static/vendor` at pinned versions. **No Node toolchain**, works offline, one process.
The API calls only `hoc.services` (enforced by `test_architecture`). SSE (not WebSocket)
because traffic is one-way and reconnection is built in; clients catch up from `?since=seq`.
The Streamlit pages remain for the M0 explorer but are no longer the main interface.

**Revisit** if the UI outgrows hand-written modules: the API is the boundary, so a Vite/React
frontend can replace `static/` without backend changes.
