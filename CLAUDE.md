# hoc-ai — guidance for Claude Code

An AI-native, graph-based learning system prototype. Read before working:
- `docs/ARCHITECTURE.md` — the architecture (authoritative). §2 lists invariants that must never be violated.
- `docs/IMPLEMENTATION_PLAN.md` — milestones M0–M7 and their acceptance checks.
- `docs/LEARNING_SIDE_PLAN.md` — the learner/tutor side (milestones L-M0…L-M6). Follow its invariants L1 when working there.

## Non-negotiable invariants
1. The knowledge graph changes **only** through `ChangeSet`s of typed `GraphOp`s (with author, rationale, evidence, confidence). Never write to concept/relation tables directly.
2. `Observation`s are append-only. Learner state is derived by a `StudentModel` from observations and may be deleted and recomputed at any time.
3. Keep the four layers separate: knowledge / assessment / learner / analytics. Analytics only produces proposals.
4. The policy decides abstract actions; the tutor only renders them. Log every `PolicyDecision`.
5. Every LLM call goes through `hoc.llm.LLMClient` (logged, cached, versioned prompt from `prompts/`).
6. Concept IDs are permanent; merges/splits use redirects.

## Conventions
- Python 3.12, `uv`, Pydantic v2 domain types in `hoc.core`, SQLite via SQLAlchemy.
- Respect the dependency direction in ARCHITECTURE §12.2.
- New strategies (student models, policies, builders, graders) implement the interfaces in ARCHITECTURE §13 and are selected via config.
- LLM steps: prompt file + output schema + recorded-fixture test. Live LLM tests are marked and skipped by default.
- Thresholds and weights belong in `config/`, not in code.
- If an implementation choice conflicts with the architecture, stop and note the conflict in `docs/IMPLEMENTATION_PLAN.md` rather than silently diverging.

## Commands
- Quality gates (all must pass before finishing a task): `ruff check .`, `ruff format --check .`, `mypy`, `pytest`.
- Run the Studio: `hoc serve` (web UI + API). Older explorer: `streamlit run app/Home.py`. CLI: `hoc --help`. Env: `HOC_DB`, `HOC_CONFIG`, `ANTHROPIC_API_KEY`.
- `tests/unit/test_architecture.py` enforces the package dependency direction. New packages must be added to its `ALLOWED` map.
- Record non-obvious decisions as ADRs in `docs/adr/`; update the status log in `docs/IMPLEMENTATION_PLAN.md` after each milestone.
- Interfaces (`cli.py`, `app/`) call `hoc.services.HocApp` only; keep them thin and free of domain logic.

## Agent team (see ADR 0005)
- Agents return proposals/feedback; only `BuildEngine` turns them into ChangeSets. Never apply from an agent.
- New agent role/implementation: subclass `LLMAgent`, `@register_agent("name")`, set it in `agents.team`; prompt in `prompts/agent_<step>/vN.md` + recorded-fixture test (`tests/conftest.py::demo_settings`).
- New LLM provider kind: class in `hoc/llm/backend.py`, register in `BACKEND_KINDS`. Interfaces under `hoc/web` may import only `hoc.services`.
- Frontend lives in `src/hoc/web/static` (ES modules, vendored libs, no build step).

## Learning side (see docs/LEARNING_SIDE_PLAN.md, ADRs 0008-0011)
- Evidence is insert-only: never add update/delete to `ObservationStore`. Beliefs are derived (`hoc.learner`) and disposable.
- Opening a node is an Interaction, not evidence. The learning side never changes the graph; gaps become `Feedback`.
- Add a student model / policy / tutor: implement the protocol, `@register_student_model` / `@register_policy` / `@register_tutor`, select with `learn.*` in config. `hoc.learner` and `hoc.policy` must not import `hoc.llm`.
- At most ONE LLM call per learner message (`TurnEngine`); validate everything the model returns; keep context sections within `learn.context` budgets (tests enforce this).
- LLM steps added here: `tutor_turn`, `tutor_grade`, `agent_task_extract`, `agent_task_match` (prompts in `prompts/`, recorded outputs in `data/examples/gcd_lcm_fixture.json`).
- `tests/conftest.py::demo_settings` runs everything from the recorded fixture with no network.

## Graph views / lenses (see ADR 0012)
- Views are presentation: named, possibly overlapping concept sets with `bridge_ids` added to connect them. They never change the graph and are never ChangeSets.
- New kind of view: write a pure provider `(ViewInputs) -> list[GraphView]` with `@register_view_provider("name")` in `hoc.views`, then list it under `views.providers`. `hoc.views` may import only core, store and config. Don't call an LLM on the read path; store LLM-proposed views as saved views.
- Frontend: reuse `useLens` / `LensPanel` / `LensBanner` / `LensToggle` from `static/views.js` on any graph screen, and pipe data through `lens.display(data)` before `groupView`.
