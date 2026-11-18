# ADR 0009 — One structured call per tutor turn

**Context.** ARCHITECTURE §9.4 says the tutor never decides the next action and a separate
evidence interpreter judges responses (§7.2). That is two or three LLM calls per message. The
learning side is cost-bounded and must not become an agent loop.

**Decision.** Interpretation, reply, move choice and check-question grading are merged into **one**
`tutor_turn` call (`TutorTurnOutput`). The policy still owns the candidate set: the tutor may only
pick an index among ≤4 moves the policy offered, or none. Everything the model returns is
validated before it has an effect: evidence needs a concept id from the context and a quote that
really occurs in the learner's message; move indexes are range-checked; check questions need a
valid target. Closed actions (what next, flags, no-exercise replies, over-budget) make no call;
exact-graded exercises make none; open answers make exactly one (`tutor_grade`). Budgets are
counted from the LLM call log, so tutor and grader share one limit.

**Consequences.** This narrows §9.4 to "the tutor never moves outside the policy's candidates".
Context is a deterministic neighbourhood with per-section character budgets (`learn.context`),
~3k input tokens per turn in practice. `TurnEngine.handle` is a plain function, so a simulated
learner can drive it later.
