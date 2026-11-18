"""HocApp: the façade used by the CLI and the web UI.

It wires storage, settings and (lazily) the LLM client. Interfaces call methods here and
never reach into ``hoc.store`` internals, so a different front end can be added without
touching domain logic.
"""

from __future__ import annotations

from pathlib import Path

from hoc.config import Settings, load_settings
from hoc.core.changes import ChangeSet
from hoc.llm import LLMClient, PromptRegistry
from hoc.llm.backend import LLMBackend, RoutingBackend
from hoc.store import (
    AttemptStore,
    CallCounter,
    ChangeSetStore,
    Database,
    DecisionStore,
    InteractionStore,
    KnowledgeGraph,
    LearnerStore,
    NotesStore,
    ObservationStore,
    SessionStore,
    SnapshotStore,
    StateCache,
    TaskStore,
    TurnStore,
)
from hoc.store.workspace import (
    EventLog,
    FeedbackStore,
    MetaStore,
    SourceStore,
    TestStore,
    ViewStore,
)


class HocApp:
    def __init__(self, settings: Settings, db: Database, backend: LLMBackend | None = None) -> None:
        self.settings = settings
        self.db = db
        self.changesets = ChangeSetStore(db)
        self.snapshots = SnapshotStore(db)
        self.meta = MetaStore(db)
        self.sources = SourceStore(db)
        self.feedback = FeedbackStore(db)
        self.tests = TestStore(db)
        self.events = EventLog(db)
        self.saved_views = ViewStore(db)
        # learning side
        self.learners = LearnerStore(db)
        self.sessions = SessionStore(db)
        self.turns = TurnStore(db)
        self.observations = ObservationStore(db)
        self.interactions = InteractionStore(db)
        self.decisions = DecisionStore(db)
        self.notes = NotesStore(db)
        self.state_cache = StateCache(db)
        self.attempts = AttemptStore(db)
        self.calls = CallCounter(db)
        self.task_store = TaskStore(db)
        self._backend = backend
        self._llm: LLMClient | None = None

    @classmethod
    def open(
        cls,
        config_path: str | Path | None = None,
        db_path: str | Path | None = None,
        backend: LLMBackend | None = None,
        settings: Settings | None = None,
    ) -> HocApp:
        settings = settings or load_settings(config_path)
        db = Database(db_path or settings.db_path)
        db.migrate()
        return cls(settings, db, backend)

    def graph(self) -> KnowledgeGraph:
        return KnowledgeGraph.load(self.db)

    @property
    def llm(self) -> LLMClient:
        """Created on first use so everything else works without API credentials."""
        if self._llm is None:
            backend = self._backend or RoutingBackend(self.settings.llm)
            prompts = PromptRegistry(self.settings.prompts_dir, self.settings.llm.prompt_versions)
            self._llm = LLMClient(backend, self.db, self.settings.llm, prompts)
        return self._llm

    def reset_llm(self, settings: Settings) -> None:
        """Swap settings (e.g. new model choices); the client is rebuilt on next use."""
        self.settings = settings
        self._llm = None

    @staticmethod
    def parse_changeset(raw: str) -> ChangeSet:
        return ChangeSet.model_validate_json(raw)

    def close(self) -> None:
        self.db.close()
