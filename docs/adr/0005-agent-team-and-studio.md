# ADR 0005 — An agent team builds the graph; the Studio makes it observable

**Context.** ARCHITECTURE §5.1 preferred a deterministic pipeline with one bounded "Curator"
agent. The product direction changed: given a topic (and optionally expert material), a *team of
agents* should build, test and maintain the graph, the expert must be able to watch and steer it
live, and the team must be easy to extend.

**Decision.**
- Five roles: **planner** (areas + grain guide), **builder** (one area per step), **critic**
  (deterministic validators + LLM review), **tester** (writes questions; traces each through the
  graph to find missing concepts/relations), **refiner** (turns feedback into proposals).
- A **coordinator** (pure function of persisted state) picks the next task: queued expert tasks →
  plan → expert feedback → build areas → review rounds (critic → tests → trace → refine), bounded
  by `max_rounds`. Because it is pure and progress is persisted, a run can stop and continue.
- Agents return **proposals** (flat `DraftOp`s) and **feedback**; they never write the graph.
  `compile_ops` translates drafts to `GraphOp`s, `ChangeSetStore.preview` dry-runs them (bad ops
  are dropped and reported), and **triage** routes the ChangeSet by run mode:
  manual = expert approves all; ask = apply when confident and non-structural, else ask;
  auto = apply all. Expert-locked elements always need the expert (ADR 0003).
- Everything that happens is an append-only **event** (SQLite `events`); the UI streams it over SSE.
- The **Runner** (the only threaded code) decides *when* to step: pause/resume/step/stop, waits
  while the expert is needed (modes manual/ask), and pauses on error.
- Roles, coordinator, triage and parsers are **registries** selected in `config/default.yaml`
  (`agents.team`, `agents.coordinator`, `agents.triage`); adding one never edits the engine.
- One topic = one SQLite file (`ProjectManager`).

**Consequences.** Deviates from "pipeline first" (§5.1) deliberately; typed steps, versioned
prompts and cached calls are kept, so each agent is still testable with recorded fixtures
(`data/examples/gcd_lcm_fixture.json` replays a real run). Free-roaming agent chat is avoided: the
coordinator, not the models, controls flow.
