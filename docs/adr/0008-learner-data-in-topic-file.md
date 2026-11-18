# ADR 0008 — Learner data lives in the topic file; evidence is insert-only

**Decision.** Learners, sessions, turns, observations, interactions, policy decisions, notes and
task attempts are stored in the **same SQLite file as the topic graph** (schema v3, additive).
Observations reference concept ids of that graph, so keeping them together keeps ids valid,
makes a topic one copyable unit, and lets one project host several learner profiles (the default
is `me`; extra profiles keep experiments and simulated learners apart). No authentication.

- `ObservationStore` has **no update or delete method** (tested). Beliefs are derived by a
  `StudentModel` and are disposable: `learner_state_cache` is only an optimisation, keyed by
  `(learner, model)` and validated against graph sequence, evidence counts, config hash and age.
- Opening a node is an `Interaction`, never evidence. "I get it / I'm lost" is both a visible
  flag and weak evidence (reliability 0.2).
- Graph change under a learner is handled in one place (`hoc.learner.resolve`): merged → redirect,
  split → successors at `learn.split_evidence_weight`, deprecated → ignored.

**Consequences.** Deleting a topic deletes its learners; exporting is `hoc learn export`
(JSON lines). Cross-topic learner profiles would need a separate store (not needed yet).
