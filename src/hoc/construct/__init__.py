"""Graph construction: an agent team that builds, tests and maintains a topic graph.

Pieces (each replaceable behind a small interface, selected in ``config/default.yaml``):
  * agents      - one class per role, registered by name (``hoc.construct.agents``);
  * coordinator - decides which agent works next (``hoc.construct.coordinator``);
  * triage      - decides which proposals need the expert (``hoc.construct.triage``);
  * engine      - runs one step: task -> agent -> ChangeSets / feedback / tests.
Agents never write the graph: they return proposals that become ChangeSets.
"""
