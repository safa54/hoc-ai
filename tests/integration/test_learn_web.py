"""The Learn API over HTTP, driven by the recorded fixture (no network)."""

import time

import pytest
from fastapi.testclient import TestClient

from hoc.web.server import create_app
from tests.conftest import demo_settings

WRONG = "It is 60, I took the smallest number that both 12 and 20 go into."


@pytest.fixture
def client(tmp_path):
    settings = demo_settings().model_copy(update={"projects_dir": str(tmp_path / "projects")})
    with TestClient(create_app(settings)) as c:
        yield c


def settle(client, base, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(base).json()["run"]
        if run["status"] == "idle" and run["detail"].startswith("Up to date"):
            return
        time.sleep(0.1)
    raise AssertionError("run did not settle")


def test_learning_flow_over_http(client):
    pid = client.post("/api/projects", json={"title": "GCD and LCM"}).json()["id"]
    base = f"/api/projects/{pid}"
    client.post(f"{base}/run", json={"action": "start", "mode": "auto"})
    settle(client, base)
    client.post(f"{base}/materials/text", json={"title": "Sheet", "text": "1. a\n\n2. b"})
    deadline = time.time() + 30
    while time.time() < deadline and len(client.get(f"{base}/tasks").json()) < 6:
        time.sleep(0.1)
    settle(client, base)
    client.post(f"{base}/run", json={"action": "pause"})

    learn = f"{base}/learn"
    assert [x["id"] for x in client.get(f"{learn}/learners").json()] == ["me"]
    other = client.post(f"{learn}/learners", json={"name": "Second Learner"}).json()
    assert other["id"] == "second-learner"
    sess = client.post(f"{learn}/sessions", json={"learner_id": "me"}).json()
    sid = sess["session"]["id"]
    assert (
        client.post(f"{learn}/sessions", json={"learner_id": "me"}).json()["session"]["id"] == sid
    )

    graph = client.get(f"{learn}/graph?learner_id=me").json()
    assert graph["total"] == 66 and graph["areas"] and graph["counts"]["unknown"] == 66
    assert any(n["ready"] for n in graph["nodes"]) and any(n["tasks"] for n in graph["nodes"])

    concept = client.get(f"{learn}/concepts/gcd-definition?learner_id=me").json()
    assert concept["concept"]["card"]["definition"] and concept["state"]["status"] == "unknown"
    assert set(concept["evidence"]) == {"recall", "apply", "explain", "transfer"}
    assert client.get(f"{learn}/concepts/nope?learner_id=me").status_code == 404

    # interactions: a visit is not evidence; a flag is weak evidence and shown separately
    client.post(
        f"{learn}/interactions",
        json={"learner_id": "me", "kind": "visit", "concept_id": "gcd-definition"},
    )
    node = next(
        n
        for n in client.get(f"{learn}/graph?learner_id=me").json()["nodes"]
        if n["id"] == "gcd-definition"
    )
    assert node["visited"] and node["status"] == "unknown" and node["n_obs"] == 0
    bad = client.post(
        f"{learn}/interactions",
        json={
            "learner_id": "me",
            "kind": "flag",
            "concept_id": "gcd-definition",
            "meta": {"flag": "nonsense"},
        },
    )
    assert bad.status_code == 400

    moves = client.post(
        f"{learn}/moves", json={"learner_id": "me", "focus": "gcd-definition", "session_id": sid}
    ).json()
    assert moves["moves"] and moves["decision_id"]
    accepted = client.post(
        f"{learn}/interactions",
        json={
            "learner_id": "me",
            "kind": "chip_accept",
            "concept_id": moves["moves"][0]["concept_id"],
            "meta": {"decision_id": moves["decision_id"], "index": moves["moves"][0]["rank"]},
        },
    )
    assert accepted.status_code == 200
    decisions = client.get(f"{learn}/decisions?learner_id=me").json()
    assert decisions[-1]["accepted"] is True and decisions[-1]["chosen_by"] == "learner"

    # the tutor: recorded real outputs, one call per message
    t1 = client.post(
        f"{learn}/turn",
        json={
            "learner_id": "me",
            "session_id": sid,
            "intent": "explain",
            "focus_concept_id": "gcd-definition",
        },
    ).json()
    assert t1["cost"]["calls"] == 1 and t1["check"] and "meta" not in t1["turn"]
    t2 = client.post(
        f"{learn}/turn",
        json={
            "learner_id": "me",
            "session_id": sid,
            "text": WRONG,
            "focus_concept_id": "gcd-definition",
        },
    ).json()
    assert t2["observations"] and t2["session_cost"]["calls"] == 2
    dbg = client.get(f"{learn}/turns/{t2['turn']['id']}/debug").json()
    assert dbg["meta"]["context"] and dbg["decision"]["candidates"] and dbg["observations"]
    nxt = client.post(
        f"{learn}/turn", json={"learner_id": "me", "session_id": sid, "intent": "next"}
    )
    assert nxt.json()["cost"]["calls"] == 0
    assert len(client.get(f"{learn}/turns?session_id={sid}").json()) == 6
    mismatch = client.post(
        f"{learn}/turn", json={"learner_id": "second-learner", "session_id": sid, "text": "hi"}
    )
    assert mismatch.status_code == 400

    # exercises: the learner never sees the answer key
    gcd_tasks = client.get(f"{learn}/tasks?concept_id=lcm-definition&learner_id=me").json()
    assert gcd_tasks and "rubric" not in gcd_tasks[0] and "solution_text" not in gcd_tasks[0]
    opened = client.post(
        f"{learn}/tasks/{gcd_tasks[0]['id']}/open", json={"learner_id": "me"}
    ).json()
    aid = opened["attempt"]["id"]
    right = client.post(f"{learn}/attempts/{aid}/answer", json={"learner_id": "me", "answer": "12"})
    assert right.json()["correct"] and right.json()["graded_by"] == "exact"

    # research views
    table = client.get(f"{learn}/map?learner_id=me").json()
    assert len(table["rows"]) == 66 and "beta-facet" in table["models"] and table["notes"]
    cmp = client.get(f"{learn}/compare?learner_id=me&a=beta-facet&b=flat-beta").json()
    assert len(cmp["rows"]) == 66
    export = client.get(f"{learn}/export?learner_id=me")
    assert export.headers["content-type"].startswith("application/x-ndjson")
    assert len(export.text.strip().splitlines()) > 8
    assert client.get(f"{learn}/observations?learner_id=me").json()
    assert client.get(f"{learn}/graph?learner_id=second-learner").json()["counts"]["unknown"] == 66
