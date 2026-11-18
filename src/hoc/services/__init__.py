"""Service façade: the only API that interfaces (CLI, web UI) should depend on."""

from hoc.services.app import HocApp
from hoc.services.learning import LearningSpace
from hoc.services.projects import ProjectManager
from hoc.services.workspace import TopicWorkspace

__all__ = ["HocApp", "LearningSpace", "ProjectManager", "TopicWorkspace"]
