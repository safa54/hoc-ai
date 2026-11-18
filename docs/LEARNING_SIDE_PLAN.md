# Learning side — architecture and implementation plan (v0.3)

> Status: implemented (L-M0…L-M6); see the status log in `IMPLEMENTATION_PLAN.md` for deviations. Audience: the implementer and the project owner.
> Read first: `CLAUDE.md` (invariants), `ARCHITECTURE.md` §4.3–4.4, §7–9 (assessment, learner,
> policy — this plan implements a first slice of them) and §18 (the Studio, which this sits beside).
> Section numbers below are prefixed **L** to avoid clashing with the architecture document.

---

## L0. What we are building

A second face of the application: **Learn**. The same topic graph that the Studio builds is now
something a person learns from. The learner opens nodes, reads them, talks to a tutor about them,
does exercises, and sees — as does the researcher — what the system believes they understand.

Research-prototype rules that shape every decision:

| Rule | Consequence |
|---|---|
| No accounts, single machine | A *learner profile* picker (default profile "me"), no auth. Extra profiles exist so experiments and simulated learners do not pollute each other. |
| Everything observable | Every tutor turn stores: what the tutor was shown, what it answered, what evidence it extracted, which options the policy offered and which was taken, and what it cost. All of it is visible in the UI. |
| Cost-bounded | **At most one LLM call per learner message, zero for many actions.** No tool loops on the learning side. Context is assembled deterministically from a neighbourhood, never the whole graph. |
| Will change a lot | Student model, policy, context builder, evidence interpreter and tutor are separate plug-ins behind small interfaces, selected in config (same pattern as `agents.team`). |

### Non-goals for this iteration
Login/multi-user, mobile layout, voice, spaced-repetition scheduling UI, simulated learners and the
offline evaluation harness (ARCH §11; the seams for them are kept), misconception library
(the types exist; mining them is a build-side task for later), token-by-token streaming of replies.

---

## L1. Invariants (in addition to `CLAUDE.md`)

1. **Observations are the only input to beliefs.** Chat turns, self-reports, exercise answers and
   check questions all become `Observation`s (append-only). Beliefs are recomputed from them;
   deleting the belief cache must change nothing.
2. **"Covered" is not "understood".** Opening or reading a node is an *interaction event*, never
   evidence. The UI shows the two separately.
3. **The policy proposes, the tutor renders, the decision is logged.** Candidate pedagogical moves
   (go to a prerequisite, use an analogy, check, practise, advance) are computed deterministically
   from graph + beliefs. The tutor call may only pick among them. Navigation chips in the UI are
   built from the structured choice, never parsed out of prose.
4. **One call per message.** The turn engine makes at most one `LLMClient.structured` call per
   learner message; closed actions (what next, self-report, exact-graded answers) make none.
5. **The learning side never changes the graph.** If the tutor or the exercise matcher notices a
   gap in the graph, it files `Feedback` for the Studio agents (the existing channel).
6. **Layers stay separate.** Tasks/materials = assessment layer; observations/beliefs = learner
   layer. Neither is stored on concepts.

---

## L2. User experience

### L2.1 Two sides, one switch
Routes: `#/` home · `#/build/<topic>` Studio (existing; keep `#/<topic>` as an alias) ·
`#/learn/<topic>` Learn. The top bar of both sides carries a two-button switch **Build | Learn**
that keeps the current topic. On the home page each topic card gets two buttons: *Build* and
*Learn*. The two sides share no panels and no state beyond the topic id.

### L2.2 Learn screen layout
```
┌ top bar: ← · topic · [Build | Learn] · learner profile ▾ · progress (12/66 understood) · cost ┐
├───────────────────────────────┬───────────────────────────────────────────────────────────────┤
│ GRAPH (same Cytoscape canvas)  │ tabs:  Learn  ·  Exercises  ·  My map  ·  Tutor's view        │
│  nodes coloured by belief      │ ┌ Reader (collapsible): name, summary, definition, key ideas, │
│  super-nodes (areas) collapse  │ │  examples; belief strip per facet; ☐ "I get it" / "Lost"    │
│  "ready next" nodes glow       │ ├ Chat: thread, suggestion chips, quick actions, input        │
│  toolbar: overview/expand,     │ └────────────────────────────────────────────────────────────│
│  colour legend, fit            │                                                               │
└───────────────────────────────┴───────────────────────────────────────────────────────────────┘
```
- **Clicking a node** sets the *focus*: the Reader shows its content and the chat continues in the
  same thread with a divider "— now looking at *X* —". Clicking empty canvas clears the focus;
  the chat is then in *overview* scope (questions about the whole topic, see L5.3).
- **Quick actions** above the input (each sends a structured `intent`, not free text):
  *Explain this* · *Why do I need this?* · *Quiz me* · *Give me an exercise* · *What next?* ·
  *I get it* · *I'm lost*.
- **Suggestion chips** under a tutor message, built from the structured move:
  "↩ Review *Divisibility* first", "≈ Compare with *GCD by listing*", "→ Next: *Euclidean
  algorithm*", "✎ Try an exercise". Clicking a chip moves the focus (and logs that the learner
  accepted the suggestion). The learner is never moved automatically.

### L2.3 What the learner sees on the graph (requirement 4)
One encoding, used consistently in graph, Reader and "My map":

| Visual | Meaning | Source |
|---|---|---|
| grey fill | unknown — no evidence yet | belief status `unknown` |
| red fill | struggling | `likely_not` |
| amber fill | partial / shaky | `uncertain` |
| light green / green fill | understood / solid | `likely` / `mastered` |
| small dot badge | visited (covered) | interaction events |
| ⚑ badge | the learner's own flag ("I get it" / "I'm lost") | self-report observation |
| glowing outline | ready next: prerequisites are in place | policy frontier |
| dashed outline | draft node (not yet accepted by the expert) | concept status |

Node fill uses the *weakest relevant* summary: the concept status is derived from its facet
beliefs (L4.3). Colours must come from a palette that survives colour-blindness (add a shape or
icon redundancy: ✓ for green, ! for red); state the palette once in `learn.css` variables.

### L2.4 What the researcher sees (requirement 4, "beliefs visible")
- **Belief strip** in the Reader: one bar per facet (recall / apply / explain / transfer) showing
  mean, evidence mass and status; click → the observations behind it (quote, outcome, judge,
  weight, when) — the `StudentModel.explain` output.
- **My map** tab: table of all concepts with status per facet, sortable; filter "struggling",
  "ready next", "never visited"; learner notes (the tutor's running narrative, with the turn ids
  it cites); a selector to recompute the same evidence with another student model (replay).
- **Tutor's view** tab (debug): for the selected tutor message — the context sections it was
  given with character/token counts, the policy candidates with score components and which was
  chosen, the raw structured output, the observations it produced, model, tokens, latency, cost;
  plus session totals and remaining budget.

---

## L3. Architecture

### L3.1 Packages and dependency direction
New packages follow ARCH §12.2. Add them to `tests/unit/test_architecture.py::ALLOWED` first.

| Package | Contents | May import |
|---|---|---|
| `hoc.core` (extend) | `core/learning.py`: new learner-side types (L3.2). Reuse `core/learner.py`, `core/assessment.py`, `core/refs.py` unchanged where possible. | — |
| `hoc.store` (extend) | `store/learning.py`: `LearnerStore`, `ObservationStore`, `ConversationStore`, `DecisionStore`, `InteractionStore`; `store/tasks.py`: `TaskStore`. Schema v3, additive. | core |
| `hoc.learner` | `StudentModel` protocol, `beta_facet`, `flat_beta`, registry, redirect/split resolution, concept-level summary. Pure, no LLM. | core, store, config |
| `hoc.policy` | `Policy` protocol, `heuristic`, `prereq_order`, registry; frontier computation. Pure, no LLM. | core, store, config, learner |
| `hoc.assess` | `TaskBank` (retrieval), deterministic graders, `Grader` protocol + LLM grader. | core, store, llm, config |
| `hoc.tutor` | `ContextBuilder`, `TurnEngine`, `Tutor` protocol + `llm_tutor`, intents, budget guard. | core, store, llm, config, learner, policy, assess |
| `hoc.construct` (extend) | `agents/materials.py`: `extractor` and `matcher` roles (build side, L7). | unchanged |
| `hoc.services` (extend) | `services/learning.py`: `LearningSpace` (peer of `TopicWorkspace`, same `HocApp`). `ProjectManager.learning(pid)`. | + learner, policy, assess, tutor |
| `hoc.web` (extend) | `web/learn_api.py`: an `APIRouter` mounted by `create_app`. Frontend: `static/learn.js`, `static/learn.css`; split current `app.js` into `app.js` (shell, routes, home, shared parts) and `studio.js`. | services, core, config |

`learner` and `policy` must stay importable without `hoc.llm` (enforced by the test above).

### L3.2 Data model (new and reused)

Reused as-is: `Learner`, `Turn`, `Episode`, `Observation`, `Outcome`, `Judge`, `LearnerAssertion`,
`PolicyDecision` (`core/learner.py`); `Task`, `Approach`, `Requirement`, `Rubric`, `SolutionPath`
(`core/assessment.py`); `TargetRef`, `Facet` (`core/refs.py`).

Small extensions to existing types (all with defaults, so old data stays valid):
- `Session.objective_id` → make optional (a free-exploration session has none).
- `Turn`: add `focus_concept_id: str | None`, `intent: str | None`, `llm_call_id: str | None`,
  `decision_id: str | None`, `meta: dict` (context section sizes, for the debug tab).
- `Task`: add `title: str = ""`, `source_chunk_id: str | None`, `match_status:
  Literal["unmatched","proposed","confirmed","rejected"] = "unmatched"`, `match_confidence: float
  = 0`, `unmapped: list[str] = []`, `solution_text: str = ""`.
- `PolicyDecision`: add `learner_id`, `focus_concept_id`, `chosen_by:
  Literal["policy","tutor","learner"]`, `accepted: bool | None` (did the learner click the chip).

New in `core/learning.py`:

```
Belief        { mean, alpha, beta, evidence_mass, status, observed: bool, inferred_mean: float|None }
BeliefStatus  = unknown | likely_not | uncertain | likely | mastered
ConceptState  { concept_id, status: BeliefStatus, facets: {facet: Belief}, visited: bool,
                self_flag: "got_it"|"lost"|None, last_evidence_at }
LearnerState  { learner_id, model_id, config_hash, graph_seq, concepts: {id: ConceptState},
                computed_at }                                   # derived, cacheable, deletable
Interaction   { id, learner_id, kind: visit|read|chip_accept|chip_dismiss|flag, concept_id, at, meta }
Move          { kind: stay|review_prerequisite|use_analogy|check|practice|advance|overview,
                concept_id: str|None, facet: Facet|None, task_id: str|None,
                reason: str, score: float, components: {name: float} }
PendingCheck  { question, target: TargetRef, expected_key_ideas: [str], asked_turn_id }
LearnerNotes  { learner_id, entries: [{ text, turn_id, at }] }  # tutor's narrative; context only
Intent        = message | explain | why_needed | quiz | exercise | next | got_it | lost | hint | answer
TutorReply    { turn (tutor Turn), move: Move|None, suggestions: [Move], observations: [Observation],
                check: PendingCheck|None, cost: {model, input_tokens, output_tokens, usd, ms} }
```

### L3.3 Storage (schema v3, additive; one topic file as today)
Learner data lives **in the topic's own SQLite file**, because observations reference concept ids
of that graph. Tables (JSON documents plus the indexed columns shown):

`learners(id)` · `learn_sessions(id, learner_id)` · `turns(id, session_id, learner_id, created_at)` ·
`observations(id, learner_id, concept_id, created_at)` — **insert-only; the store exposes no
update/delete** · `interactions(id, learner_id, concept_id, created_at)` ·
`policy_decisions(id, learner_id, created_at)` · `learner_notes(learner_id)` ·
`learner_state_cache(learner_id, model_id, config_hash, graph_seq)` ·
`tasks(id, match_status, source_id)` · `task_targets(task_id, concept_id, weight)` (retrieval index,
rebuilt from the task document on save).

Bump `SCHEMA_VERSION` to 3. Add an ADR (0008) recording "learner data lives in the topic file".

### L3.4 Graph changes under a learner
The graph keeps evolving while someone learns. Implement ARCH §4.6 in one place,
`hoc.learner.resolve`: merged concept → follow `redirect_to`; split concept → evidence is copied to
each successor at `learn.split_evidence_weight` (default 0.5) and marked inferred; deprecated →
ignored (kept in the log). The state cache key includes `graph_seq`, so any applied ChangeSet
invalidates it. `learn.visible_statuses` (default `[accepted, proposed]`) decides which nodes the
learner sees; proposed ones are drawn dashed.

---

## L4. Student model

### L4.1 Contract (`hoc.learner.base`)
```python
class StudentModel(Protocol):
    id: str

    def compute(
        self,
        observations: list[Observation],
        graph: KnowledgeGraph,
        at: datetime,
        settings: StudentModelSettings,
    ) -> dict[str, dict[Facet, Belief]]: ...
    def explain(self, observations, target: TargetRef) -> list[BeliefContribution]: ...
```
Pure and deterministic. `LearnerState` assembly (visited, flags, concept-level status) is shared
code in `hoc.learner.state`, not per model. Registry + `learn.student_model` config key, as with
agents.

### L4.2 `beta-facet` (default) — ARCH §8.2, simplified
- Per (concept, facet): Beta(α, β), prior (1, 1). Update with observation weight `w`:
  strong α+=w · partial α+=0.5w, β+=0.5w · absent β+=0.3w · contradicting β+=w.
- Weight = `judge.reliability × requirement_weight × 1/(1+hints_used)`; reliabilities in config:
  exact 1.0, rule 0.9, llm 0.7, self_report 0.2.
- Evidence mass `m = α+β−2`. Status: `m < 1 → unknown`; else mean `<0.35 likely_not`, `<0.65
  uncertain`, `<0.85 likely`, else `mastered` (needs `m ≥ 2.5`). All thresholds in config.
- Decay at read time: counts above the prior × `2^(−Δdays / half_life)` (default 14 days).
- Cross-facet propagation and `inferred_mean` from essential prerequisites exactly as §8.2;
  `inferred_mean` is shown as a hint, never counted as evidence, never coloured as understood.
- Second implementation `flat-beta` (one Beta per concept) so the seam is exercised and the
  "replay with another model" control has something to switch to.

### L4.3 Concept-level status (what colours a node)
`unknown` if every facet is unknown; otherwise computed from the observed facets:
`likely_not` if any observed facet is `likely_not`; `mastered` if apply and explain are both
`mastered`; `likely` if apply ≥ likely and no observed facet below `uncertain`; else `uncertain`.
Keep this in one function with a table-driven test — it will be tuned.

---

## L5. The tutor turn (cost-bounded by construction)

### L5.1 Flow for one learner message
```
POST /learn/turn { session_id, text, intent, focus_concept_id }
 1. store learner Turn                                              (no LLM)
 2. state  = LearnerState (cache or recompute from observations)    (no LLM)
 3. moves  = policy.candidates(state, graph, focus, bank)           (no LLM) → top-k (k ≤ 4)
 4. if intent ∈ {next, got_it, lost}: answer from template + moves  (no LLM) → go to 8
 5. budget guard: session/day limits; over budget → polite fixed reply, no call
 6. ctx    = ContextBuilder.build(focus, state, moves, thread, pending_check)   (no LLM)
 7. out    = llm.structured("tutor_turn", ctx.variables, TutorTurnOutput)       (THE one call)
 8. validate out: evidence ids ⊆ context ids; quotes must occur in the learner's text;
    move index within range (else "stay")
 9. persist: tutor Turn (+ llm_call_id), Observations, PendingCheck, notes entry,
    PolicyDecision(candidates, chosen, chosen_by="tutor")
10. return TutorReply (state delta for the graph: changed concept statuses)
```
`TurnEngine.handle()` is a plain function of its inputs and stores; the HTTP layer only calls it.
That is also the seam for a simulated learner later (ARCH §13 `io`).

### L5.2 The single prompt: `prompts/tutor_turn/v1.md`
Output schema (flat, portable — same rules as `construct/drafts.py`):
```
TutorTurnOutput {
  reply: str                      # markdown; ≤ ~180 words unless the learner asked for depth
  move: int                       # index into the offered moves; -1 = just answer
  evidence: [ { concept_id, facet: recall|apply|explain|transfer,
                outcome: strong|partial|absent|contradicting, quote, rationale } ]
  check_question: str             # "" if none; a question the learner should answer next
  check_concept_id: str, check_facet: str, check_expected: [str]
  check_result: "" | strong | partial | absent | contradicting     # verdict on the pending check
  note: str                       # ≤ 1 sentence for the learner notes, "" if nothing new
  graph_gap: str                  # "" or: the learner needed something the graph lacks
}
```
Prompt rules to encode: teach from the focus card, do not invent content beyond it and its listed
neighbours; evidence only for what the learner's *own words in this message* show (asking a
question is not `contradicting`; silence is not evidence); pick a move only when it helps — when
the learner's message shows a gap in a listed prerequisite pick `review_prerequisite`, when a
listed understood neighbour makes a good bridge pick `use_analogy`; ask a check question at most
every other turn; never claim to have changed the learner's status. `graph_gap` becomes a
`Feedback(kind=issue, author=agent "tutor")` for the Studio (invariant L1.5).

### L5.3 Context builder (requirement 5)
`hoc.tutor.context.ContextBuilder` — a list of **sections**, each with a hard character budget
from config; sections are rendered in a fixed order and their sizes recorded on the Turn.

| Section | Content | Default budget |
|---|---|---|
| `focus` | full card of the focus node (summary, definition, key ideas, ≤2 examples) | 1800 |
| `prerequisites` | direct prerequisites: name, one-line, necessity, learner status | 900 |
| `leads_to` | direct dependents: name, one-line, status | 500 |
| `bridges` | `analogous_to` / `contrasts_with` / `specializes` neighbours: name, one-line, status | 500 |
| `outline` | areas (super-nodes): title, node count, learner coverage (e.g. "6/9 understood") | 500 |
| `learner` | focus-facet beliefs, flags, last 5 notes, struggling concepts (names only) | 600 |
| `moves` | the ≤4 candidate moves with reasons (index, kind, target name) | 400 |
| `pending_check` | the open check question and its expected ideas | 300 |
| `thread` | last `learn.context.turns` (default 6) turns, older ones dropped | 1800 |
| `exercises` | titles of ≤3 matched tasks for the focus (ids, not bodies) | 200 |

Never included: cards of non-neighbours, the whole relation list, source documents.
**Overview scope** (no focus, or intent `overview`): `focus`/`prerequisites`/`leads_to`/`bridges`
are replaced by `map` — every area with its concept *names* and statuses (names only: ~66 names ≈
1.5 k chars) and the top frontier. That is what answers "where am I?", "how does this topic hang
together?", "what should I do today?" without loading any card.

Target size ≈ 1.5–2.5 k input tokens per turn; `llm.max_tokens` for the step ≈ 900. Add per-step
`max_tokens` to `LLMSettings` (`step_max_tokens: dict[str,int]`) — it is global today.
Default tier for `tutor_turn`: `standard`; it must work acceptably on `fast` (try it).

### L5.4 Budget guard (`hoc.tutor.budget`)
Config `learn.budget`: `max_llm_calls_per_session` (default 120), `max_llm_calls_per_day` (400),
`max_input_chars_per_call` (computed from section budgets; assert in tests). Counts come from
`llm_calls` filtered by step. The top bar shows calls and estimated cost for the session; fill
`llm.pricing` so the number is real.

### L5.5 Check questions without a second call
When the tutor asks a check question it is stored as `PendingCheck`. The learner's next message
is then judged *inside the same `tutor_turn` call* (`check_result`), producing an observation on
the check's target with the facet the question aimed at. No separate grading call.

---

## L6. Policy (`hoc.policy`)

Contract: `candidates(state, graph, focus, bank, settings) -> list[Move]`, sorted by score, pure.
`heuristic` (default) generates:

| Move | Generated when | Main score components |
|---|---|---|
| `review_prerequisite(p)` | essential prerequisite `p` of focus is `unknown`/`uncertain`/`likely_not` | weakness of `p`, necessity, recent negative evidence on focus |
| `use_analogy(a)` | an analogous/contrasting/specialising neighbour `a` is ≥ `likely` | strength of `a`, relation type |
| `check(focus, facet)` | a facet of focus is unknown, or apply ≥ likely while explain is unknown (superficial pattern, ARCH §7.3) | information gain, depth gap |
| `practice(task)` | the bank has a confirmed task for focus not seen recently | apply-facet uncertainty |
| `advance(n)` | `n` is in the frontier (all essential prerequisites ≥ likely, `n` not yet ≥ likely); prefer dependents of focus, then same area | readiness, proximity, area continuity |
| `stay` | always | constant baseline |

Weights in `config: learn.policy.weights`. Frontier = ARCH §9.1. Second implementation
`prereq-order` (topological order, advance when `likely`) as the baseline. Every surfaced set of
candidates is written as a `PolicyDecision`; a later chip click sets `accepted`. This log is the
dataset for comparing policies offline later.

---

## L7. Exercises and materials (requirement 7)

### L7.1 Build side — getting tasks into the bank and matched
Studio gains a **Materials** tab. Flow:
1. Expert uploads an exercise sheet / problem set (PDF, text) *as material* → stored as a `Source`
   of type `problem_set` (existing ingest). It is **not** fed to the planner.
2. **Extractor** (`agent_task_extract`, one call per chunk group): splits text into `Task`s —
   prompt, solution text if present, task type, options for MCQ. `match_status = unmatched`.
   The expert can also add a single task by hand.
3. **Matcher** (`agent_task_match`): for a batch of ≤ 8 tasks, receives the graph outline plus, per
   task, a lexical shortlist of ≤ 12 candidate concepts (name, one-line) — not the whole graph. It
   returns per task: `approaches` (each an AND-set of concept + facet + primary/secondary — the
   Q-matrix row of ARCH §4.3), confidence, and `unmapped` ideas the graph lacks.
4. Routing reuses the run mode: confidence ≥ `triage.auto_confidence` → `confirmed` (auto/ask),
   otherwise `proposed` for the expert, who confirms/edits/rejects in the Materials tab
   (task text, linked nodes highlighted on the graph).
5. `unmapped` ideas become `Feedback(kind=issue, target=graph)` → the refiner may add the missing
   concept — the same loop the tester already feeds.

Implementation notes: both roles are registered agents (`agents.team: extractor, matcher`); the
coordinator schedules them when unmatched material/tasks exist (after expert feedback, before
review rounds) — add a `materials` phase to `PhasedCoordinator` rather than a second run loop.
Tasks are assessment-layer records written through `TaskStore`; they are **not** ChangeSets
(they do not change the knowledge graph). When a concept a task points to is merged/split, links
are resolved through redirects at read time; a deprecated target sets the task back to `proposed`.
Later: promote the tester's `GraphTest`s into tasks.

### L7.2 Learn side — using tasks
- `TaskBank.for_concept(concept_id, facet=None, exclude_ids, limit)`: confirmed tasks whose
  requirements include the concept, primary first, unseen first. Deterministic, no LLM.
- **Exercises** tab for the focus node: list → open one → answer box → *Check* / *Hint* /
  *Show solution*.
- Grading: `mcq`/`numeric` → exact (no LLM, judge `exact`). Open answers → one
  `tutor_grade` call returning per-requirement outcomes, the approach used, and feedback text;
  observations are attributed to the targets of the approach the learner used (ARCH §7.2).
  *Hint* is a normal tutor turn with intent `hint` and the task in context; each hint raises
  `hints_used` (discounting later evidence). *Show solution* records an `absent` observation with
  low weight and no further evidence from that attempt.
- The tutor's `practice` move opens a task through the same path.

---

## L8. Super-nodes (requirement 6; independent of everything else)

Use what exists: every concept already carries its area in `topic_ids`, and areas are listed in
the blueprint. Super-nodes are a **view**, not new dependency semantics (topics stay
"navigation only", ARCH §4.1).

- `graph.js` gets a pure function `groupView(data, expandedAreas) → {nodes, edges}`: for each
  collapsed area emit one super-node (title, node count; on the Learn side a progress ring from
  member statuses) and aggregate edges between areas/nodes (deduplicated, weight = count).
  Expanded areas show their member nodes as today. No compound-node layout is needed: a
  super-node is an ordinary node, so dagre keeps working.
- Controls: *Overview* (collapse all) · double-click a super-node to expand it · *Expand all*.
  Default: overview when the graph has more than `ui.collapse_threshold` (30) nodes.
- Works on both sides; the Studio keeps change animations (a change inside a collapsed area
  flashes the super-node).
- Manual regrouping: `update_concept` already allows patching `topic_ids`; add "Move to area…"
  in the Studio inspector. Graph-level topic ops (rename/split areas, an agent that regroups)
  are deferred — note them in the plan's status log, do not build them now.
- Fallback if aggregated layouts look poor: evaluate `elkjs` + `cytoscape-elk` (true compound
  layout) in a spike; do not add it by default (≈1.4 MB).

---

## L9. API (`web/learn_api.py`, all under `/api/projects/{pid}/learn`)

| Method & path | Purpose |
|---|---|
| `GET /learners` · `POST /learners` | list / create learner profiles |
| `POST /sessions` `{learner_id}` | start (or resume the latest) session → thread + state |
| `GET /state?learner_id&model=` | `LearnerState` (optionally recomputed with another model) + frontier |
| `GET /graph?learner_id` | graph JSON + per-node status/visited/flag + areas (one request for the canvas) |
| `GET /concepts/{id}?learner_id` | card + neighbours with statuses + beliefs + `explain` contributions + tasks available |
| `POST /interactions` | visit / read / chip accept / dismiss |
| `POST /turn` `{session_id, text, intent, focus_concept_id}` | the tutor turn (L5.1) → `TutorReply` |
| `GET /turns?session_id` | thread |
| `GET /turns/{id}/debug` | context sections + sizes, candidates, raw output, observations, cost |
| `GET /tasks?concept_id&learner_id` · `POST /tasks/{id}/answer` · `POST /tasks/{id}/hint` | exercises |
| `GET /decisions?learner_id` · `GET /observations?learner_id` | research tables |
| `GET /export?learner_id` | JSONL of turns, observations, decisions, interactions |

Build-side additions under the existing prefix: `GET/POST /tasks`, `POST /materials/file|text`,
`POST /tasks/{id}/match` (confirm/edit/reject), `POST /run {action: "match"}`.

Live updates are not needed on the Learn side (request/response); do not add SSE there.

---

## L10. Configuration (add to `config/default.yaml`)

```yaml
learn:
  student_model: beta-facet        # hoc.learner registry
  policy: heuristic                # hoc.policy registry
  tutor: llm                       # hoc.tutor registry
  visible_statuses: [accepted, proposed]
  split_evidence_weight: 0.5
  judge_reliability: { exact: 1.0, rule: 0.9, llm: 0.7, self_report: 0.2 }
  beliefs: { unknown_mass: 1.0, likely_not: 0.35, uncertain: 0.65, likely: 0.85,
             mastered_mass: 2.5, half_life_days: 14 }
  policy_weights: { weakness: 1.0, necessity: 0.6, recent_failure: 0.8, readiness: 1.0,
                    proximity: 0.4, depth_gap: 0.7, info_gain: 0.5 }
  context: { turns: 6, focus: 1800, prerequisites: 900, leads_to: 500, bridges: 500,
             outline: 500, learner: 600, moves: 400, pending_check: 300, thread: 1800,
             exercises: 200, map: 2500 }
  budget: { max_llm_calls_per_session: 120, max_llm_calls_per_day: 400 }
llm:
  step_tiers: { tutor_turn: standard, tutor_grade: standard,
                agent_task_extract: fast, agent_task_match: standard }
  step_max_tokens: { tutor_turn: 900, tutor_grade: 700 }
```
Typed in `hoc.config` as `LearnSettings` (strict models, like `AgentSettings`). Per-project
overrides reuse the existing `settings_overrides` mechanism; expose `tutor_turn`/`tutor_grade`
model choice in the Learn side's settings popover.

---

## L11. Milestones (implement in this order; each ends green on all quality gates)

Each milestone: domain types → store → logic → service → API → UI, with tests at every layer.
LLM steps need a prompt file, an output schema and a **recorded-fixture test** (extend
`data/examples/` with a `learn_fixture.json`; record it from one real session the same way the
GCD/LCM fixture was recorded). Update `docs/IMPLEMENTATION_PLAN.md` status log after each.

**L-M0 — Shell and separation.** Split `app.js` into `app.js` (routes, home, shared components),
`studio.js`, `learn.js` (placeholder). Routes `#/build/<pid>`, `#/learn/<pid>`, alias `#/<pid>`.
Build|Learn switch in both top bars; two buttons on home cards. Add new packages (empty, with
docstrings) and `ALLOWED` entries. *Done when:* existing tests pass, both routes render, switching
keeps the topic, no Studio regression (repeat the tab sweep).

**L-M1 — Learner store, student model, coloured graph, reader (no LLM).** Types, schema v3,
stores (observations insert-only — test that no update path exists), `beta-facet` + `flat-beta`,
state assembly with redirect/split resolution, `LearningSpace` (profiles, sessions, state, graph,
concept detail, interactions, self-report). Learn UI: canvas coloured per L2.3, Reader with belief
strip and "I get it / I'm lost", My map table. *Done when:* a self-report changes a node's flag
(and, weakly, its belief); deleting the state cache changes nothing; a table-driven test covers
status thresholds, decay, propagation, merge-redirect and split inheritance; switching the model
in My map re-derives from the same log.

**L-M2 — Policy and "what next" (no LLM).** `heuristic` + `prereq-order`, frontier, decision log,
glow on ready-next nodes, *What next?* quick action answered from templates, chips with
accept/dismiss logging. *Done when:* on the recorded GCD/LCM graph with scripted observations the
policy proposes the expected prerequisite for a failed node and the expected frontier; every
surfaced candidate set has a `PolicyDecision`.

**L-M3 — Tutor chat.** `ContextBuilder`, `TurnEngine`, `tutor_turn/v1.md`, validation, pending
checks, notes, budget guard, `graph_gap` → Feedback, Tutor's view tab, cost in top bar,
`step_max_tokens`. *Done when:* (a) a test asserts **exactly one** backend request per message and
zero for `next/got_it/lost`; (b) a test asserts every context section respects its budget and no
non-neighbour card appears; (c) hallucinated evidence (unknown id, quote not in the message) is
dropped; (d) over-budget sessions make no call; (e) a real session of ~15 turns on GCD/LCM is
recorded as the fixture and its per-turn input stays under ~3 k tokens; (f) in the browser: open
node → explain → wrong answer to a check → chip to the prerequisite appears and the node turns
amber/red.

**L-M4 — Super-nodes.** `groupView`, overview/expand controls on both sides, progress rings on the
Learn side, "Move to area…" in the Studio. Independent: may be done any time after L-M0.
*Done when:* the 66-node graph opens as ~7 super-nodes with aggregated edges, expands in place,
and Studio change animations still show.

**L-M5 — Exercise bank.** Build side: Materials tab, extractor, matcher, `materials` coordinator
phase, review of proposed matches, unmapped → Feedback. Learn side: `TaskBank`, Exercises tab,
exact graders, `tutor_grade`, hints, `practice` move. *Done when:* an uploaded sheet of ~10
GCD/LCM exercises yields tasks matched to plausible nodes (recorded fixture); a correct numeric
answer produces an `exact` observation without any LLM call; an open answer makes exactly one.

**L-M6 — Research conveniences.** Overview scope polish, export (`hoc learn export`, JSONL),
replay comparison view (two models side by side), observations/decisions tables.

Deferred, with seams already in place: simulated learners and the evaluation harness (ARCH §11),
misconceptions in the tutor loop, spaced review, streaming replies, objectives/paths, an agent
that maintains areas.

---

## L12. Testing and verification checklist
- Unit (no LLM): student models, concept status table, resolve (merge/split), policy candidates,
  frontier, context budgets, validation of tutor output, graders, task retrieval, budget guard.
- Recorded-fixture: `tutor_turn`, `tutor_grade`, `agent_task_extract`, `agent_task_match`.
- Integration (FastAPI `TestClient`, fixture provider): full learn flow over HTTP; build-side
  materials flow; architecture test with the new packages.
- Browser: the L-M3(f) scenario and the L-M4 scenario, plus the Studio tab sweep for regressions.
- Gates: `ruff check .`, `ruff format --check .`, `mypy`, `pytest` — all must pass per milestone.

## L13. Decisions to record as ADRs while implementing
- **0008** Learner data lives in the topic file; observations insert-only; state cache keyed by
  `(learner, model, config_hash, graph_seq)`.
- **0009** One structured call per tutor turn: interpretation, reply and move selection are merged
  for cost; the policy still owns the candidate set; evidence is validated against the context.
  (This narrows ARCH §9.4 "the tutor never decides the next action" to "never outside the policy's
  candidates" — say so explicitly.)
- **0010** Tasks are assessment-layer records, matched by build-side agents, not ChangeSets.
- **0011** Super-nodes are a view over `topic_ids`, not graph semantics.

## L14. Open questions for the project owner (defaults are chosen; none blocks implementation)
1. Tutor model: default is the `standard` tier (Sonnet). Prefer `fast` (Haiku) for cost?
2. Should draft (not yet expert-approved) nodes be visible to the learner? Default: yes, dashed.
3. Should "I get it" count as evidence at all? Default: yes, weakly (0.2) — it is visible as a
   flag either way.
4. Language of tutoring: default follows the topic brief's `language`.
