# hoc-ai

AI-native, graph-based learning system prototype. Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Build plan and status: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).
Conventions for contributors (human or Claude Code): [`CLAUDE.md`](CLAUDE.md).

## Quickstart

```bash
uv sync                      # or: python -m venv .venv && pip install -e . pytest ruff mypy types-PyYAML types-networkx
export HOC_DB=data/hoc.db    # optional; default comes from config/default.yaml
uv run hoc init
uv run hoc demo seed         # loads a tiny intro-probability graph
uv run hoc graph show
uv run streamlit run app/Home.py   # web UI: graph explorer + change history/review actions
```

### Graph Studio (agents build a graph while you watch)

```bash
uv run hoc serve             # http://127.0.0.1:8000 — create a topic, add PDFs/notes, press Start
```

Pick *I approve everything* / *Agents ask me* / *Fully automatic*, pause or step the team, click any
node to inspect or edit it, leave feedback on anything, add test questions, and scrub the history
slider. To try it without an API key or cost, set every role to the `demo` provider in **Setup**
(`demo:x`): it replays a recorded run.

**Any LLM:** providers live in `config/default.yaml` under `llm.providers` (Anthropic, OpenAI,
OpenRouter, Gemini, Ollama, any OpenAI-compatible server). Keys come from environment variables
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …). Choose models per agent role in the UI as
`provider:model`, e.g. `openai:gpt-5.1` or `ollama:qwen3:32b`.

### Learning from the graph

Open a topic and switch **Build | Learn** (top bar). In **Learn**, click a node to read it, talk to the
tutor about it (quick actions: explain, why it matters, quiz me, exercise, what next), and watch the
node colours: grey = unknown, red = struggling, amber = shaky, green = understood, with a dot for
visited and ⚑/⚐ for your own "I get it / I'm lost". Large graphs open as area super-nodes; click one
to expand it. **My map** shows what the system believes, per facet, with the evidence behind it,
lets you re-derive beliefs with another student model, and exports everything as JSON lines;
**Tutor's view** shows exactly what the tutor was given and why it answered as it did.
Upload an exercise sheet in **Build → Materials** and the agents link its exercises to concepts.
Each tutor message costs at most one model call (budgets: `learn.budget`).

Everything except agent runs and tutoring (store, CLI, tests) works without an API key.

## Quality gates (run before every commit)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                  # strict
uv run pytest                # includes the architecture/dependency-direction test
HOC_LIVE=1 uv run pytest -m live   # optional real-API smoke test
```

## Layout

| Path | Role |
|---|---|
| `src/hoc/core` | Domain types (pydantic). No I/O, no dependencies on other hoc packages. |
| `src/hoc/store` | SQLite store, `GraphTx` write path, ChangeSet lifecycle, `KnowledgeGraph` read model, snapshots. |
| `src/hoc/llm` | `LLMClient` (prompt registry, structured output, cache, retry, call log) and backends. |
| `src/hoc/learner`, `policy`, `assess`, `tutor` | Student models, pedagogical policy, task bank and graders, the one-call tutor engine. |
| `src/hoc/ingest` | PDF/text parsing and chunking. |
| `src/hoc/construct` | The agent team, coordinator, triage, validators, draft-op compiler, engine. |
| `src/hoc/services` | `HocApp`, `TopicWorkspace`, `ProjectManager`, `Runner`. The only API the CLI/web depend on. |
| `src/hoc/web` | FastAPI API + SSE and the Studio SPA (`static/`, no build step). |
| `src/hoc/cli.py`, `app/` | Interfaces: Typer CLI and the older Streamlit explorer. Thin. |
| `prompts/<step>/vN.md` | Versioned prompt templates. |
| `config/default.yaml` | Model tiers, per-step tiers, retry/cache settings, pricing. |
| `docs/adr/` | Architecture decision records. Add one when you make a non-obvious decision. |

## Status

M0 (foundation), M0b (Graph Studio) and the learning side (L-M0…L-M6) done. See the plan for what remains.
