# ADR 0012: Graph views ("lenses") are computed presentation, not knowledge

**Context.** Large graphs are hard to navigate. Experts and learners want to look at one
meaningful part of the graph at a time: an area, what a question needs, what an exercise needs,
a high-level overview, or a selection of their own. These parts overlap and should be
connected subgraphs.

**Decision.**
- A `GraphView` (`hoc.core.views`) is a named list of concept ids. It adds `bridge_ids`
  (concepts added only to connect the members), `focus_ids`, `kind` (area / subtopic /
  question / exercise / overview / path / custom) and `connected`. Views never change the
  graph, so they need no ChangeSet. Like super-nodes (ADR 0011), they are presentation.
- `hoc.views` (it may import only core, store and config) holds the pure algorithms:
  - `connect`, which greedily adds shortest paths of at most `views.max_bridge_hops` hops. It
    uses `related` edges only as a last resort.
  - `importance`, `needed_for` and `neighbourhood`.
  - A registry of **view providers**, `@register_view_provider`, enabled in order under
    `views.providers`. The built-in providers are `overview` (topic backbone and starting
    points), `areas` (the partition, not connected), `questions` (latest trace of each graph
    test), `exercises` (requirements of confirmed tasks) and `saved`.
- On-demand views ("everything needed for X", "around X") are computed per request.
- Saved views (`SavedView`) are stored per topic in the `saved_views` meta document through
  `ViewStore`. No schema change is needed. Saved views are kept exactly as their author
  chose them, with no bridges added.
- `services.ViewService` serves views to every interface under `/api/projects/{pid}/views`,
  with these routes: GET the list; GET `needed-for/{cid}` and `around/{cid}`; POST to save;
  DELETE `{id}`.
- In the frontend, `views.js` has the reusable `useLens` hook, `LensPanel`, `LensBanner`,
  `LensToggle` and the pure `applyView`. Build and Learn use the same pieces. Inside a view,
  every area is expanded and the graph is laid out again for the view alone. An optional
  "context" ring shows neighbours one step outside the view, faded.
- Area partitions are now visible when expanded. `groupView(..., { frames: true })` draws
  each open area as a Cytoscape compound node (a box). Clicking the box title collapses it
  into the super-node, and clicking the super-node expands it again. `cytoscape-dagre` lays
  out compound nodes directly.

**Consequences.** Any feature that needs "the part of the graph about X" can reuse a
provider, for example the tutor's context or an evaluation slice. An LLM step that proposes
subtopics can be added later as a provider whose results are stored as saved views. It must
not run on the read path, because listing views has to stay free.

**Deferred.**
- An agent that proposes subtopic and overview views.
- Ranking views for a learner (e.g. "views where you are weakest").
- Steiner-optimal connection; the greedy version is small, deterministic and good enough.
