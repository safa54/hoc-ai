# ADR 0013 — Scoped prompts, simplicity budgets, and one learning path

**Context.** Three problems showed up on real topics (57–140 concepts). (1) Cost: the refiner
and the critic were sent every card of the graph on every call (about 200k characters per call
at 140 concepts, the refiner on the `strong` tier), and every review round re-traced every test.
(2) Growth: nothing in the loop rewarded a small graph. Critic and tester only ever asked for
more, so a school topic reached 70 concepts. (3) Navigation: "advance" offered any ready concept
in alphabetical topological order, so suggestions hopped between unrelated areas, and the map
showed no route.

**Decision.**
- *Scoped graph text* (`construct.context.render_graph`). Every agent gets a one-line outline
  (summaries clipped, relations grouped by source). Full cards are shown only where needed: the
  refiner sees the concepts its notes are about (`mentioned_concepts` plus the trace of a test
  note); the critic sees concepts whose version changed since its last review
  (`Progress.reviewed_versions`, at most `agents.review_batch` per review) and makes no model
  call when the graph did not change (`Progress.reviewed_seq`).
- *Fewer calls.* Tests whose last trace was fully covered are not re-traced in later rounds
  (`agents.retrace_covered`). Agent notes below `agents.refine_min_severity` stay open for the
  expert but never cost a refiner call; expert notes are always acted on. Prerequisite edges
  implied by a chain at least as strong are removed by the critic as an ordinary proposal,
  without a model (`validators.redundant_prerequisite_edges`; an essential edge is only
  redundant next to an all-essential chain, because the learning frontier follows essential
  edges). Defaults: 2 review rounds, 3 refiner steps per round.
- *Simplicity budgets* in config, stated in the v2 prompts: `agents.max_areas`,
  `agents.area_concepts`, `agents.max_findings`. The planner plans the smallest graph that
  covers the brief; the builder folds variants and pitfalls into cards; the critic looks for
  what to merge or remove before what to add, and flags areas over twice the budget; the
  refiner prefers dismiss > edit > relation > merge > add > split; the tester reports an idea
  as missing only if no concept covers it even broadly.
- *One learning path* (`policy.path.learning_path`, pure). It respects prerequisites, prefers
  what the last concept leads to, finishes an area before opening the next, and takes areas in
  planner order. With a goal it is only the goal and what it essentially needs. The learner
  sets a goal with a `goal` interaction (an interface action, never evidence). The heuristic
  policy adds a `path` component to "advance" moves and ranks further alternatives lower
  (`learn.policy_weights.path`, `.alternative`), so the map and the tutor follow the same
  route. `/learn/graph` carries the path; `/learn/path` returns it alone.
- *Glow instead of filter.* `glowView` (frontend, pure) lights up a set of concepts inside the
  whole graph. It is used for the learner's path (next steps numbered, the next one pulsing,
  super-nodes that contain it glow), for any lens (`✨ glow` in the banner, Studio and Learn),
  and for concepts the tutor chooses: `TutorTurnOutput.highlight`, validated against the
  context ids, no extra call. Views stay presentation (ADR 0012).
- *Overview first.* Learn opens as super-nodes above `learn.collapse_threshold` (now 12), laid
  out as a grid in planner order; opening one moves the camera to it and lays it out by its own
  edges.

**Consequences.** Measured on the stored 139-concept topic: refiner prompt about 209k -> 50k
characters, outline for builder/tester/planner 52k -> 34k; later critic reviews read only
changed cards. A graph larger than `review_batch` is reviewed over several rounds and may end
a run partly reviewed. Parked low-severity notes are visible in Feedback but not resolved by
agents. Existing graphs are not shrunk automatically: "Review again" lets the v2 critic and
refiner propose merges.

**Deferred.** Provider-side prompt caching (stable prefix first); moving the refiner off the
`strong` tier; a build-side agent that proposes saved views.
