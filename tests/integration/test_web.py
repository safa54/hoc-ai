import time

import pytest
from fastapi.testclient import TestClient

from hoc.web.server import create_app
from tests.conftest import demo_settings


@pytest.fixture
def client(tmp_path):
    settings = demo_settings().model_copy(update={"projects_dir": str(tmp_path / "projects")})
    with TestClient(create_app(settings)) as c:
        yield c


def wait_idle(client, pid, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/projects/{pid}").json()["run"]
        if run["status"] == "idle" and run["detail"].startswith("Up to date"):
            return
        time.sleep(0.1)
    raise AssertionError("run did not settle")


def test_studio_flow_over_http(client):
    assert client.get("/").status_code == 200
    created = client.post(
        "/api/projects",
        json={"title": "GCD and LCM", "audience": "Grades 6-10", "description": "gcd, lcm"},
    ).json()
    pid = created["id"]
    base = f"/api/projects/{pid}"
    assert [p["id"] for p in client.get("/api/projects").json()] == [pid]

    # input at any time: pasted text and an uploaded file
    assert (
        client.post(f"{base}/sources/text", json={"title": "Notes", "text": "gcd\n\nlcm"}).json()[
            "chunks"
        ]
        == 1
    )
    upload = client.post(f"{base}/sources/file", files={"file": ("n.md", b"# More\n\ntext")})
    assert upload.status_code == 200 and len(client.get(f"{base}/sources").json()) == 2
    assert client.post(f"{base}/sources/file", files={"file": ("n.docx", b"x")}).status_code == 400

    # run automatically, watch the graph appear
    assert client.post(f"{base}/run", json={"action": "start", "mode": "auto"}).status_code == 200
    wait_idle(client, pid)
    graph = client.get(f"{base}/graph").json()
    assert len(graph["nodes"]) >= 50 and graph["latest_seq"] > 0 and graph["areas"]

    # time travel
    early = client.get(f"{base}/graph?at=1").json()
    assert 0 < len(early["nodes"]) < len(graph["nodes"])

    # inspect any node
    cid = graph["nodes"][0]["id"]
    detail = client.get(f"{base}/concepts/{cid}").json()
    assert detail["concept"]["card"]["one_line"] and detail["relations"] is not None
    edge = graph["edges"][0]["id"]
    assert client.get(f"{base}/relations/{edge}").json()["relation"]["id"] == edge
    assert client.get(f"{base}/concepts/nope").status_code == 404

    # graph views (lenses), shared by Build and Learn
    views = client.get(f"{base}/views").json()
    assert {"overview", "area"} <= {v["kind"] for v in views}
    assert client.get(f"{base}/views/needed-for/{cid}").json()["focus_ids"] == [cid]
    assert client.get(f"{base}/views/around/{cid}?depth=2").json()["kind"] == "path"
    vid = client.post(f"{base}/views", json={"title": "Mine", "concept_ids": [cid]}).json()["id"]
    assert any(v["id"] == vid and v["saved"] for v in client.get(f"{base}/views").json())
    assert client.post(f"{base}/views", json={"title": "", "concept_ids": [cid]}).status_code == 400
    assert client.delete(f"{base}/views/{vid}").status_code == 200
    assert client.delete(f"{base}/views/{vid}").status_code == 404

    # manual edit locks and is attributed to the expert
    edit = client.post(
        f"{base}/edit",
        json={
            "ops": [{"op": "add_concept", "concepts": [{"name": "Mine", "summary": "x"}]}],
            "rationale": "by hand",
        },
    )
    assert edit.status_code == 200 and edit.json()["status"] == "approved"
    bad = client.post(
        f"{base}/edit", json={"ops": [{"op": "remove_concept", "concept_id": "ghost"}]}
    )
    assert bad.status_code == 400 and "unknown concept" in bad.json()["detail"]
    assert client.post(f"{base}/edit", json={"ops": [{"op": "nonsense"}]}).status_code == 400

    # feedback + tests + events
    fb = client.post(f"{base}/feedback", json={"text": "Add more", "kind": "directive"}).json()
    assert fb["status"] == "open"
    t = client.post(f"{base}/tests", json={"question": "gcd(4,6)?", "trace_now": False}).json()
    assert any(x["id"] == t["id"] for x in client.get(f"{base}/tests").json())
    events = client.get(f"{base}/events?limit=1000").json()
    assert events and events[-1]["seq"] >= events[0]["seq"]
    later = client.get(f"{base}/events?since={events[-1]['seq']}").json()
    assert all(e["seq"] > events[-1]["seq"] for e in later)

    # model choice per agent
    llm = client.put(f"{base}/llm", json={"roles": {"builder": "demo:y"}}).json()
    assert llm["roles"]["builder"]["override"] == "demo:y"
    assert client.get(f"{base}/llm").json()["providers"]["demo"]["kind"] == "fixture"

    assert client.delete(base).status_code == 200
    assert client.get("/api/projects").json() == []


def test_unknown_routes_and_projects(client):
    assert client.get("/api/projects/nope").status_code == 404
    assert client.get("/api/whatever").status_code == 404
    assert client.post("/api/projects", json={"title": "  "}).status_code == 400
