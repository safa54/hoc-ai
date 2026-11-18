"""Learn-side HTTP API. Mounted by ``create_app``; calls only ``hoc.services`` (LearningSpace).

All routes live under ``/api/projects/{pid}/learn``. Request/response only: the learning side
needs no live push, so there is no SSE here.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from hoc.services import LearningSpace, ProjectManager


class LearnerIn(BaseModel):
    name: str


class SessionIn(BaseModel):
    learner_id: str
    new: bool = False


class InteractionIn(BaseModel):
    learner_id: str
    kind: str
    concept_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class TurnIn(BaseModel):
    learner_id: str
    session_id: str
    text: str = ""
    intent: str = "message"
    focus_concept_id: str | None = None


class AnswerIn(BaseModel):
    learner_id: str
    answer: str
    session_id: str | None = None


class HintIn(BaseModel):
    learner_id: str
    session_id: str
    focus_concept_id: str | None = None


class OpenTaskIn(BaseModel):
    learner_id: str


class MovesIn(BaseModel):
    learner_id: str
    focus: str | None = None
    session_id: str | None = None


def build_router(projects: ProjectManager) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{pid}/learn")

    def space(pid: str) -> LearningSpace:
        return projects.learning(pid)

    @router.get("/learners")
    def learners(pid: str) -> list[dict[str, Any]]:
        return space(pid).learners()

    @router.post("/learners")
    def create_learner(pid: str, body: LearnerIn) -> dict[str, Any]:
        return space(pid).create_learner(body.name)

    @router.post("/sessions")
    def session(pid: str, body: SessionIn) -> dict[str, Any]:
        return space(pid).session(body.learner_id, new=body.new)

    @router.get("/graph")
    def graph(pid: str, learner_id: str = "me", model: str | None = None) -> dict[str, Any]:
        return space(pid).graph(learner_id, model)

    @router.get("/path")
    def path(pid: str, learner_id: str = "me") -> dict[str, Any]:
        return space(pid).path(learner_id)

    @router.get("/concepts/{cid}")
    def concept(
        pid: str, cid: str, learner_id: str = "me", model: str | None = None
    ) -> dict[str, Any]:
        return space(pid).concept(learner_id, cid, model)

    @router.post("/interactions")
    def interact(pid: str, body: InteractionIn) -> dict[str, Any]:
        return space(pid).interact(body.learner_id, body.kind, body.concept_id, body.meta)

    @router.post("/moves")
    def moves(pid: str, body: MovesIn) -> dict[str, Any]:
        return space(pid).next_moves(body.learner_id, body.focus, body.session_id)

    @router.post("/turn")
    def turn(pid: str, body: TurnIn) -> dict[str, Any]:
        return space(pid).turn(
            body.learner_id, body.session_id, body.text, body.intent, body.focus_concept_id
        )

    @router.get("/turns")
    def turns(pid: str, session_id: str) -> list[dict[str, Any]]:
        return space(pid).turns(session_id)

    @router.get("/turns/{turn_id}/debug")
    def turn_debug(pid: str, turn_id: str) -> dict[str, Any]:
        return space(pid).turn_debug(turn_id)

    @router.get("/sessions/{session_id}/cost")
    def session_cost(pid: str, session_id: str) -> dict[str, Any]:
        return space(pid).session_cost(session_id)

    @router.get("/tasks")
    def tasks(pid: str, concept_id: str, learner_id: str = "me") -> list[dict[str, Any]]:
        return space(pid).tasks(learner_id, concept_id)

    @router.post("/tasks/{task_id}/open")
    def open_task(pid: str, task_id: str, body: OpenTaskIn) -> dict[str, Any]:
        return space(pid).open_task(body.learner_id, task_id)

    @router.post("/attempts/{attempt_id}/answer")
    def answer(pid: str, attempt_id: str, body: AnswerIn) -> dict[str, Any]:
        return space(pid).answer_task(body.learner_id, attempt_id, body.answer, body.session_id)

    @router.post("/attempts/{attempt_id}/hint")
    def hint(pid: str, attempt_id: str, body: HintIn) -> dict[str, Any]:
        return space(pid).hint_task(
            body.learner_id, attempt_id, body.session_id, body.focus_concept_id
        )

    @router.post("/attempts/{attempt_id}/reveal")
    def reveal(pid: str, attempt_id: str, body: OpenTaskIn) -> dict[str, Any]:
        return space(pid).reveal_task(body.learner_id, attempt_id)

    @router.get("/compare")
    def compare(pid: str, a: str, b: str, learner_id: str = "me") -> dict[str, Any]:
        return space(pid).compare(learner_id, a, b)

    @router.get("/export")
    def export(pid: str, learner_id: str = "me") -> Response:
        rows = space(pid).export_rows(learner_id)
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        return Response(
            body,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{pid}-{learner_id}.jsonl"'},
        )

    @router.get("/map")
    def learner_map(pid: str, learner_id: str = "me", model: str | None = None) -> dict[str, Any]:
        return space(pid).map(learner_id, model)

    @router.get("/observations")
    def observations(pid: str, learner_id: str = "me") -> list[dict[str, Any]]:
        return space(pid).observations(learner_id)

    @router.get("/decisions")
    def decisions(pid: str, learner_id: str = "me") -> list[dict[str, Any]]:
        return space(pid).decisions(learner_id)

    @router.get("/interactions")
    def interactions(pid: str, learner_id: str = "me") -> list[dict[str, Any]]:
        return space(pid).interactions(learner_id)

    return router
