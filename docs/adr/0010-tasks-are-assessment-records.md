# ADR 0010 — Exercises are assessment records matched by agents, not ChangeSets

**Decision.** Tasks live in the assessment layer (`tasks`, `task_targets`), not in the knowledge
graph. An *extractor* agent splits uploaded exercise sheets (source type `problem_set`, never fed
to the planner or builders) into tasks; a *matcher* agent links each to concepts as approaches
(AND-sets of concept + facet + primary/secondary, ARCHITECTURE §4.3) from a lexical shortlist of
≤12 candidates, not the whole graph. Routing mirrors triage: confident matches go live in
auto/ask mode, others wait for the expert (Materials tab: confirm, edit links, reject, re-match);
manual mode never confirms by itself. Ideas the graph lacks become `Feedback` for the refiner.
Links follow merges and splits at read time. Learners never see the answer key or solution.

**Consequences.** Tasks need no review through the graph's ChangeSet machinery and cannot corrupt
the graph; the cost is a second small review queue. Promoting the tester's `GraphTest`s into tasks
is a later step.
