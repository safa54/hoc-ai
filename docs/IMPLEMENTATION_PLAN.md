# hoc-ai — Implementation Plan for v0.1

This plan turns [`ARCHITECTURE.md`](ARCHITECTURE.md) into ordered milestones. Each milestone ends in something runnable and testable. Section numbers (§) refer to the architecture document.

General rules for every milestone:
- Domain types first (in `hoc.core`), then persistence, then logic, then CLI, then UI.
- Every LLM step has: a versioned prompt file, a Pydantic output schema, cached calls, and at least one recorded-fixture test.
- No milestone may bypass the ChangeSet mechanism for graph changes or write learner state directly.

---

## M0 — Skeleton, domain types, storage  ✅ DONE

**Build**
- `uv` project, `src/hoc` package layout (§12.2), `config/default.yaml`, pytest setup.
- `hoc.core`: all domain types from §4 (Concept, ConceptCard, Relation, Misconception, Topic, Source, Chunk, LLMCall, Run, EvidenceLink, ChangeSet, GraphOp, TargetRef, Task, SolutionPath, LearningObjective, Learner, Session, Episode, Turn, Observation, LearnerAssertion, LearnerNotes, PolicyDecision, AggregateFinding). Edge-type registry.
- `hoc.store`: SQLite repositories; `apply_changeset` (validates ops, writes before/after snapshots, materialises), `revert_changeset`, redirects for merge/split, snapshots.
- `hoc.llm`: `LLMClient` (Anthropic SDK, structured output via tool-use), prompt registry, input-hash cache, `llm_calls` logging, cost tracking, per-step model tiers from config.
- CLI: `hoc init`, `hoc graph show`, `hoc changeset list|show|revert`.

**Done when**
- A hand-written ChangeSet (add 3 concepts, 2 relations, then merge two concepts) applies, materialises, reverts, and redirects resolve correctly — covered by tests.
- A trivial structured LLM call is logged and served from cache on rerun.

## M1 — Ingest, extraction, consolidation

**Build**
- `hoc.ingest`: PDF/Markdown parsing, segmentation, chunk role classification, exercise–solution pairing.
- `hoc.construct` stages 2–4: grain guide, per-chunk extraction, blocking (rapidfuzz + embeddings), merge adjudication, concept-card synthesis with citations.
- Output as ChangeSets (status `proposed`, nothing applied yet).
- CLI: `hoc ingest <path>`, `hoc build --stages extract,consolidate`.

**Done when**
- On 2–3 pilot sources, the run produces concept proposals with cards whose key ideas cite chunks; re-running without changes costs zero LLM calls.

## M2 — Relations, solution paths, misconceptions, validation, triage

**Build**
- Stage 5 solution-path analysis → Tasks with `requirements` (alternatives), SolutionPaths, unmapped-use candidates.
- Stage 6 relation proposal with the definitional / substitution / confusion tests.
- Stage 7 misconception mining.
- Stage 8 validators (deterministic + LLM critic) → Issues.
- Stage 9 triage (§6.1) → auto-apply or review items.
- `toc-baseline` GraphBuilder.
- CLI: `hoc build` (full), `hoc issues`.

**Done when**
- Full build on the pilot corpus yields an accepted-plus-proposed graph whose prerequisite subgraph is a DAG, with tasks linked to concepts, and a non-empty, prioritised review queue.

## M3 — Review UI and graph explorer

**Build**
- Streamlit app: *Sources* (chunks with roles), *Graph Explorer* (Cytoscape view, filter by edge type/status/topic, concept card view with evidence), *Review Queue* (ChangeSet diff with neighbourhood before/after, structured questions, issues; approve/reject/edit; locking), direct expert editing via expert ChangeSets.
- Calibration report (acceptance rate by step × confidence bucket).
- Curator agent (§5.4) invocable from the UI on an issue or topic.

**Done when**
- An expert can work through the top 20 review items in one sitting, and every decision is visible as a ChangeSet with author = expert.

## M4 — Assessment: task bank, generation, evidence interpretation

**Build**
- Task bank queries by target/facet/type.
- Task generator for all v0.1 task types with rubric + requirements; blind-solve validation.
- Evidence interpreter (exact/rule for closed tasks; LLM for open) producing Observations, assertions, misconception hits/non-hits, approach attribution, weights (§7.2).
- Grader-validation set format in `data/gold/` and an eval command for agreement.

**Done when**
- For a fixed set of hand-written responses, the interpreter outputs observations with correct targets and plausible outcomes; agreement report runs.

## M5 — Student model, policy, tutoring loop

**Build**
- `StudentModel` interface; `beta-facet`, `flat-beta`, `bkt`; replay from observations; state cache.
- `Policy` interface; `heuristic` (§9.2), `random`, `prerequisite-order`; `PolicyDecision` logging.
- Task provider (bank-first, then generate).
- Tutor renderer with hints and episode handling; `io` abstraction.
- UI: *Tutor* page (choose objective, chat with math rendering, hint button, "why this?" showing the decision rationale) and *Learner Inspector* (facet heatmap over the graph, unknown vs. likely_not clearly distinguished, misconception beliefs, assertions, notes, evidence behind each belief).

**Done when**
- A human can complete a 20-minute session on one objective; every belief in the inspector can be traced to observations and turns; switching student model re-derives state from the same log.

## M6 — Evaluation harness

**Build**
- Gold-graph import and graph comparison metrics (§11.1), including `toc-baseline` vs `pipeline`.
- LearnerSimulator with hidden state profiles (e.g. 6 profiles with different planted misconceptions and facet gaps).
- Batch runner: `(policy × student model × simulator profile × seed)` → metrics (§11.2); replay-based predictive evaluation.
- *Runs & Evals* UI page with result tables.

**Done when**
- `hoc eval sim --policies heuristic,prerequisite-order,random --models beta-facet,flat-beta` runs end-to-end and produces a comparison report.

## M7 — Analytics → proposals

**Build**
- Aggregate findings over the observation store (§10) and conversion into `learner_aggregate` ChangeSets that always enter the review queue.

**Done when**
- Running analytics over simulated-learner logs, where a planted misconception is common, produces a reviewable proposal linking it to the right concepts.

---

## Suggested order of work with Claude Code

Implement milestone by milestone, one session per milestone or sub-part. At the start of each session, point Claude Code at `CLAUDE.md`, this plan and the relevant architecture sections. Close each milestone by running its "done when" checks and updating this file with notes on any deviations from the architecture.

---

## Status log

### M0 — done
Delivered: domain types (`hoc.core`), SQLite store with ChangeSet apply/approve/reject/revert, locks, merge/split with
redirects, snapshots, `KnowledgeGraph` read model, `LLMClient` (prompts, cache, retry, logging, tiers), CLI, and a Streamlit UI
(Home, Graph Explorer, Change History with approve/reject/revert). 50 tests; ruff + mypy --strict clean.

Deviations from the architecture (see `docs/adr/`):
- `sqlite3` + JSON documents instead of SQLAlchemy (ADR 0001).
- Revert by recorded before-states instead of per-op inverses (ADR 0002).
- Task `requirements` is modelled as `approaches: list[Approach]` (each an AND-set of `Requirement`).
- The web UI starts at M0 (graph + change history) rather than M3, so every later milestone is visible as it lands.

Nothing outstanding from M0. Graph validators and triage start in M2.

### M0b — Graph Studio (agent-built graphs, observable) — done
Delivered: `hoc.ingest` (PDF via pypdf, text/Markdown); `hoc.construct` (planner, builder, critic,
tester, refiner; phased coordinator; threshold triage; deterministic validators; draft-op compiler);
`ChangeSetStore.preview/diff/applied/touching` + `graph_at` time travel; workspace stores (brief,
sources, feedback, graph tests, event log); `Runner` (pause/resume/step); `TopicWorkspace` /
`ProjectManager`; provider-agnostic LLM layer (Anthropic, any OpenAI-compatible endpoint, fixture
replay, per-role model choice); FastAPI + SSE API and the Studio SPA (`hoc serve`).
A real run on "GCD and LCM" (grades 6–10) produced 66 concepts / 108 relations, 4 traced tests and
2 refinement rounds; that run is recorded in `data/examples/gcd_lcm_fixture.json` and replayed by
the tests and by the `demo` provider.

Deviations from the architecture (ADRs 0005–0007): agent team instead of pipeline-first for
construction; FastAPI+SPA instead of Streamlit; schema v2 adds workspace tables (additive).
M1/M2 items still open: embeddings/fuzzy entity resolution, solution-path analysis, misconception
mining, task bank. Known limits: whole graph is in each prompt (fine for hundreds of nodes);
auto-applied elements stay `proposed` until the expert approves them; feedback targeting a
relation is passed to agents as text only.

### Learning side (L-M0 … L-M6) — done
Plan and architecture: [`LEARNING_SIDE_PLAN.md`](LEARNING_SIDE_PLAN.md); decisions: ADRs 0008–0011.
Delivered: **Build | Learn** split (frontend into `ui.js`, `studio.js`, `learn.js`, `chat.js`,
`exercises.js`; routes `#/build/<topic>`, `#/learn/<topic>`); `hoc.learner` (student models
`beta-facet` and `flat-beta`, merge/split resolution, state assembly), `hoc.policy` (heuristic and
prereq-order policies, frontier, decision log), `hoc.tutor` (context builder with per-section
budgets, one-call turn engine with validation, budget guard), `hoc.assess` (task bank, exact and
LLM graders); the exercise pipeline on the build side (extractor, matcher, Materials tab) and the
Exercises tab on the learn side; super-nodes on both sides; research views (My map with model
comparison, tutor's view, evidence and decision logs, JSONL export, `hoc learn export`).
Verified live with Claude on GCD/LCM: tutor turns ~2.9–3.3k input tokens, one call each; the real
extractor/matcher/tutor/grader outputs are recorded in `data/examples/gcd_lcm_fixture.json` and
replayed by `tests/unit/test_learn_replay.py`.

Deviations from the plan: `likely` concept status needs *apply or explain* understood (plan:
apply); budgets are counted from the LLM call log rather than turn metadata; super-nodes show a
text progress bar and status border (Cytoscape pie slices did not render legibly); graders and the
call counter live in `hoc.assess` / `hoc.store.learning`; the real-session fixture is ~3 tutor
turns, not 15.
Known limits / not done: simulated learners and the offline evaluation harness; misconceptions in
the tutor loop; spaced review; streaming replies; per-task model pickers in the Learn UI (models
are chosen in the Studio's Setup tab, which now lists tutor/grader/extractor/matcher roles);
the Studio does not flash a collapsed super-node when a change lands inside it; area-level graph
operations; the policy is deliberately simple and its weights are untuned.

### Graph views (lenses) and visible partitions — done
Decision: ADR 0012. New packages and modules: `hoc.core.views` (GraphView, SavedView),
`hoc.views` (`connect`, `needed_for`, `neighbourhood`, `importance`, and the provider registry
with `overview`, `areas`, `questions`, `exercises` and `saved`), `ViewStore` (meta document,
no schema change), `services.ViewService`, routes under `/api/projects/{pid}/views`, and the
`views:` block in config.

Frontend: `views.js` (`useLens`, `LensPanel`, `LensBanner`, `LensToggle`, `applyView`) is used
by both Studio and Learn. Expanded areas are drawn as boxes (compound nodes, "area boxes"
toggle): click a box's title to collapse it, and click the super-node to expand it. Inside a
view, all areas open and the graph is laid out again for the view, with an optional faded
context ring. Expert and learner can create, edit and save their own views by clicking
concepts.

Tests: `tests/unit/test_views.py` and HTTP checks in `tests/integration/test_web.py`.

Not done: an agent that proposes subtopic and overview views; ranking views for a learner.
Dagre's compound layout stacks areas, so the full graph gets tall; views are the intended way
to navigate it.

### Cost, simplicity, learning path, glow (ADR 0013)

Scoped graph text for every agent (outline for all, full cards only for what a step looks at);
the critic skips unchanged graphs and reads only changed cards; covered tests are not re-traced;
low-severity agent notes are parked; redundant prerequisite edges are pruned without a model.
v2 prompts for planner, builder, critic, refiner and tester-trace state simplicity budgets
(`agents.max_areas`, `area_concepts`, `max_findings`). `policy.path.learning_path` gives one
coherent route (optionally to a goal set by a `goal` interaction) that the heuristic policy
scores against and the map draws. Frontend: grid overview of super-nodes, camera moves to an
opened area, `glowView` for the path, lenses and tutor-chosen highlights (`tutor_turn` v2).

Tests: `tests/unit/test_simplicity_and_path.py`, two additions in `tests/unit/test_tutor.py`.

Not done: prompt caching; a build-side agent that proposes views; shrinking existing graphs
(run "Review again" so the v2 critic and refiner propose merges).
