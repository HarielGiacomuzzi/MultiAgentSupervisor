import json

import pytest
from fastapi.testclient import TestClient

from main import app, get_llm
from tests.fakes import failing_llm, scripted_llm


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def client_with(llm):
    app.dependency_overrides[get_llm] = lambda: llm
    return TestClient(app)


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_run_returns_final_output_and_steps():
    client = client_with(scripted_llm(["DELEGATE: researcher\nTASK: a", "FINAL: the answer"]))
    r = client.post("/run", json={"task": "Explain X", "max_iterations": 3})

    assert r.status_code == 200
    body = r.json()
    assert body["task"] == "Explain X"
    assert body["final_output"] == "the answer"
    assert body["iterations"] == 2
    assert body["forced"] is False
    assert [s["type"] for s in body["steps"]][0] == "start"
    assert any(s["type"] == "worker_result" and s["agent"] == "researcher" for s in body["steps"])


def test_max_iterations_defaults_to_5():
    client = client_with(scripted_llm(["FINAL: ok"]))
    assert client.post("/run", json={"task": "x"}).json()["steps"][0]["max_iterations"] == 5


@pytest.mark.parametrize("payload", [
    {"task": ""},
    {"task": "   "},
    {"task": "x" * 4001},
    {"task": "x", "max_iterations": 0},
    {"task": "x", "max_iterations": 11},
    {},
])
def test_invalid_input_is_rejected_without_calling_llm(payload):
    llm = scripted_llm([])
    r = client_with(llm).post("/run", json=payload)
    assert r.status_code == 422
    assert llm.calls == []


def test_run_llm_failure_returns_502():
    r = client_with(failing_llm).post("/run", json={"task": "x"})
    assert r.status_code == 502
    assert "boom" in r.json()["detail"]


def read_sse(client, payload):
    with client.stream("POST", "/run/stream", json=payload) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


def test_run_stream_emits_sse_events():
    events = read_sse(client_with(scripted_llm(["DELEGATE: researcher\nTASK: a", "FINAL: done\nline 2"])), {"task": "x"})
    assert events[0]["type"] == "start"
    assert events[-1] == {"type": "final", "output": "done\nline 2", "iterations": 2, "forced": False}


def test_run_stream_llm_failure_ends_with_error_event():
    events = read_sse(client_with(failing_llm), {"task": "x"})
    assert events[-1] == {"type": "error", "message": "RuntimeError: boom"}


def test_index_serves_chat_ui():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="task-form"' in r.text
    assert 'data-agent="supervisor"' in r.text


def test_static_assets_are_served():
    client = TestClient(app)
    for path in ("/static/app.js", "/static/sse.js", "/static/styles.css"):
        assert client.get(path).status_code == 200, path
