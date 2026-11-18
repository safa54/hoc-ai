"""FastAPI app. Thin by design: every route calls a ProjectManager/TopicWorkspace method.

Live updates use Server-Sent Events backed by the persisted event log, so the browser sees
the same history whether the agents run in this process or elsewhere.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import Response
from starlette.types import Scope

from hoc.config import Settings, load_settings
from hoc.core.build import Brief
from hoc.core.errors import HocError, NotFoundError
from hoc.services import ProjectManager, TopicWorkspace
from hoc.web.learn_api import build_router as build_learn_router

STATIC = Path(__file__).parent / "static"


class NoCacheStatic(StaticFiles):
    """Always revalidate: the frontend changes constantly during development."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class BriefIn(BaseModel):
    title: str
    description: str = ""
    audience: str = ""
    language: str = "English"


class TextSourceIn(BaseModel):
    title: str = ""
    text: str


class DecisionIn(BaseModel):
    decision: Literal["approve", "apply", "reject"]
    reason: str = ""


class EditIn(BaseModel):
    ops: list[dict[str, Any]]
    rationale: str = ""
    lock: bool = True


class FeedbackIn(BaseModel):
    text: str
    kind: str = "comment"
    target_kind: str = "graph"
    target_id: str | None = None
    severity: str = "medium"


class ResolveIn(BaseModel):
    status: str
    note: str = ""


class AnswerIn(BaseModel):
    text: str


class TestIn(BaseModel):
    question: str
    expected: str = ""
    trace_now: bool = True


class TaskIn(BaseModel):
    prompt: str
    title: str = ""
    solution: str = ""
    task_type: str = "short_answer"
    options: list[str] = Field(default_factory=list)
    correct_answers: list[str] = Field(default_factory=list)


class TaskDecisionIn(BaseModel):
    action: str
    approaches: list[dict[str, Any]] | None = None


class ViewIn(BaseModel):
    title: str
    concept_ids: list[str]
    kind: str = "custom"
    description: str = ""
    focus_ids: list[str] = Field(default_factory=list)
    author: str = "expert"
    id: str | None = None


class ControlIn(BaseModel):
    action: str
    mode: str | None = None


class ModelsIn(BaseModel):
    roles: dict[str, str | None] = Field(default_factory=dict)
    tiers: dict[str, str] | None = None


def create_app(settings: Settings | None = None, manager: ProjectManager | None = None) -> FastAPI:
    projects = manager or ProjectManager(settings or load_settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        projects.close()

    app = FastAPI(title="hoc-ai graph studio", lifespan=lifespan)
    app.state.projects = projects

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, e: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(HocError)
    async def hoc_error(_: Request, e: HocError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=400)

    def ws(pid: str) -> TopicWorkspace:
        return projects.get(pid)

    app.include_router(build_learn_router(projects))

    # ------------------------------------------------------------- projects

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return projects.list()

    @app.post("/api/projects")
    def create_project(body: BriefIn) -> dict[str, Any]:
        w = projects.create(Brief(**body.model_dump()))
        return {"id": Path(w.app.db.path).stem, **w.snapshot()}

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str) -> dict[str, str]:
        projects.delete(pid)
        return {"deleted": pid}

    @app.get("/api/projects/{pid}")
    def snapshot(pid: str) -> dict[str, Any]:
        return ws(pid).snapshot()

    @app.put("/api/projects/{pid}/brief")
    def set_brief(pid: str, body: BriefIn) -> dict[str, Any]:
        return ws(pid).set_brief(Brief(**body.model_dump())).model_dump()

    # -------------------------------------------------------------- sources

    @app.get("/api/projects/{pid}/sources")
    def sources(pid: str) -> list[dict[str, Any]]:
        return ws(pid).sources()

    @app.get("/api/projects/{pid}/sources/{sid}")
    def source_text(pid: str, sid: str) -> dict[str, str]:
        return {"text": ws(pid).source_text(sid)}

    @app.post("/api/projects/{pid}/sources/text")
    def add_text(pid: str, body: TextSourceIn) -> dict[str, Any]:
        return ws(pid).add_source(body.title or "Expert notes", text=body.text)

    @app.post("/api/projects/{pid}/sources/file")
    async def add_file(
        pid: str, file: Annotated[UploadFile, File()], title: Annotated[str, Form()] = ""
    ) -> dict[str, Any]:
        data = await file.read()
        name = file.filename or "upload"
        return await asyncio.to_thread(ws(pid).add_source, title or name, filename=name, data=data)

    @app.delete("/api/projects/{pid}/sources/{sid}")
    def remove_source(pid: str, sid: str) -> dict[str, str]:
        ws(pid).remove_source(sid)
        return {"deleted": sid}

    # ------------------------------------------------------------- materials

    @app.post("/api/projects/{pid}/materials/text")
    def add_material_text(pid: str, body: TextSourceIn) -> dict[str, Any]:
        return ws(pid).add_material(body.title or "Exercises", text=body.text)

    @app.post("/api/projects/{pid}/materials/file")
    async def add_material_file(
        pid: str, file: Annotated[UploadFile, File()], title: Annotated[str, Form()] = ""
    ) -> dict[str, Any]:
        data = await file.read()
        name = file.filename or "upload"
        return await asyncio.to_thread(
            ws(pid).add_material, title or name, filename=name, data=data
        )

    @app.get("/api/projects/{pid}/tasks")
    def tasks(pid: str) -> list[dict[str, Any]]:
        return ws(pid).tasks()

    @app.post("/api/projects/{pid}/tasks")
    def add_task(pid: str, body: TaskIn) -> dict[str, Any]:
        return ws(pid).add_task(
            body.prompt,
            title=body.title,
            solution=body.solution,
            task_type=body.task_type,
            options=body.options,
            correct_answers=body.correct_answers,
        )

    @app.post("/api/projects/{pid}/tasks/{task_id}/decision")
    def task_decision(pid: str, task_id: str, body: TaskDecisionIn) -> dict[str, Any]:
        return ws(pid).task_decision(task_id, body.action, body.approaches)

    # ---------------------------------------------------------------- graph

    @app.get("/api/projects/{pid}/graph")
    def graph(pid: str, at: int | None = None) -> dict[str, Any]:
        return ws(pid).graph(at)

    # ---------------------------------------------------------------- views
    # Graph views / lenses (ADR 0012), shared by Build and Learn.

    @app.get("/api/projects/{pid}/views")
    def views(pid: str) -> list[dict[str, Any]]:
        return projects.views(pid).all()

    @app.get("/api/projects/{pid}/views/needed-for/{cid}")
    def view_needed_for(pid: str, cid: str) -> dict[str, Any]:
        return projects.views(pid).needed_for(cid)

    @app.get("/api/projects/{pid}/views/around/{cid}")
    def view_around(pid: str, cid: str, depth: int | None = None) -> dict[str, Any]:
        return projects.views(pid).around(cid, depth)

    @app.post("/api/projects/{pid}/views")
    def save_view(pid: str, body: ViewIn) -> dict[str, Any]:
        return projects.views(pid).save(
            body.title,
            body.concept_ids,
            kind=body.kind,
            description=body.description,
            focus_ids=body.focus_ids,
            author=body.author,
            view_id=body.id,
        )

    @app.delete("/api/projects/{pid}/views/{view_id}")
    def delete_view(pid: str, view_id: str) -> dict[str, str]:
        projects.views(pid).delete(view_id)
        return {"deleted": view_id}

    @app.get("/api/projects/{pid}/concepts/{cid}")
    def concept(pid: str, cid: str) -> dict[str, Any]:
        return ws(pid).concept(cid)

    @app.get("/api/projects/{pid}/relations/{rid:path}")
    def relation(pid: str, rid: str) -> dict[str, Any]:
        return ws(pid).relation(rid)

    @app.get("/api/projects/{pid}/history")
    def history(pid: str) -> list[dict[str, Any]]:
        return ws(pid).history()

    @app.post("/api/projects/{pid}/edit")
    def edit(pid: str, body: EditIn) -> dict[str, Any]:
        return ws(pid).expert_edit(body.ops, body.rationale, body.lock)

    # ------------------------------------------------------------ proposals

    @app.get("/api/projects/{pid}/changesets")
    def changesets(pid: str, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return ws(pid).changesets(status, limit)

    @app.post("/api/projects/{pid}/changesets/{cs_id}/decision")
    def decide(pid: str, cs_id: str, body: DecisionIn) -> dict[str, Any]:
        return ws(pid).decide(cs_id, body.decision, body.reason)

    @app.post("/api/projects/{pid}/changesets/{cs_id}/revert")
    def revert(pid: str, cs_id: str) -> dict[str, Any]:
        return ws(pid).revert(cs_id)

    # ------------------------------------------------------------- feedback

    @app.get("/api/projects/{pid}/feedback")
    def feedback(pid: str, status: str | None = None) -> list[dict[str, Any]]:
        return ws(pid).feedback(status)

    @app.post("/api/projects/{pid}/feedback")
    def add_feedback(pid: str, body: FeedbackIn) -> dict[str, Any]:
        return ws(pid).add_feedback(**body.model_dump())

    @app.post("/api/projects/{pid}/feedback/{fid}/resolve")
    def resolve(pid: str, fid: str, body: ResolveIn) -> dict[str, Any]:
        return ws(pid).resolve_feedback(fid, body.status, body.note)

    @app.post("/api/projects/{pid}/feedback/{fid}/answer")
    def answer(pid: str, fid: str, body: AnswerIn) -> dict[str, Any]:
        return ws(pid).answer(fid, body.text)

    # ---------------------------------------------------------------- tests

    @app.get("/api/projects/{pid}/tests")
    def tests(pid: str) -> list[dict[str, Any]]:
        return ws(pid).tests()

    @app.post("/api/projects/{pid}/tests")
    def add_test(pid: str, body: TestIn) -> dict[str, Any]:
        return ws(pid).add_test(body.question, body.expected, body.trace_now)

    @app.post("/api/projects/{pid}/tests/{tid}/trace")
    def trace(pid: str, tid: str) -> dict[str, str]:
        ws(pid).trace(tid)
        return {"queued": tid}

    @app.post("/api/projects/{pid}/tests/{tid}/active")
    def set_active(pid: str, tid: str, active: bool = True) -> dict[str, Any]:
        return ws(pid).set_test_active(tid, active)

    # ------------------------------------------------------------------ run

    @app.post("/api/projects/{pid}/run")
    def control(pid: str, body: ControlIn) -> dict[str, Any]:
        return ws(pid).control(body.action, body.mode)

    @app.get("/api/projects/{pid}/events")
    def events(pid: str, since: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return ws(pid).events(since, limit)

    @app.get("/api/projects/{pid}/events/stream")
    async def stream(pid: str, request: Request, since: int = 0) -> StreamingResponse:
        w = ws(pid)
        return StreamingResponse(
            _event_stream(request, w.events_after, w.app.events.last_seq, since),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------ settings

    @app.get("/api/projects/{pid}/llm")
    def llm(pid: str) -> dict[str, Any]:
        return ws(pid).llm_settings()

    @app.put("/api/projects/{pid}/llm")
    def set_llm(pid: str, body: ModelsIn) -> dict[str, Any]:
        return ws(pid).set_models(body.roles, body.tiers)

    # ------------------------------------------------------------ frontend

    app.mount("/static", NoCacheStatic(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/{rest:path}")
    def unknown_api(rest: str) -> None:
        raise HTTPException(404, f"no API route /api/{rest}")

    return app


async def _event_stream(
    request: Request,
    fetch: Callable[[int], list[dict[str, Any]]],
    last_seq: Callable[[], int],
    since: int,
) -> AsyncIterator[str]:
    """Push events after ``since`` (default: from now; the client loads history itself)."""
    last = since if since > 0 else await asyncio.to_thread(last_seq)
    idle = 0
    yield "retry: 2000\n\n"
    while not await request.is_disconnected():
        batch = await asyncio.to_thread(fetch, last)
        for event in batch:
            last = event["seq"]
            yield f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"
        if batch:
            idle = 0
            continue
        idle += 1
        if idle % 40 == 0:
            yield ": keep-alive\n\n"
        await asyncio.sleep(0.35)
