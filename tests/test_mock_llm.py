import asyncio

from app import llm as claude
from app import mock_llm
from app.orchestrator import run_task
from main import get_llm
from tests.fakes import collect


def test_mock_runs_full_team_then_finishes(monkeypatch):
    monkeypatch.setattr(mock_llm, "DELAY", 0)
    events = asyncio.run(collect(run_task("Explain X", 5, mock_llm.complete)))

    workers = [e["agent"] for e in events if e["type"] == "worker_result"]
    assert workers == ["researcher", "writer", "reviewer"]
    assert events[-1]["type"] == "final"
    assert events[-1]["forced"] is False
    assert events[-1]["iterations"] == 4
    assert "MOCK_LLM" in events[-1]["output"]


def test_mock_honours_forced_completion(monkeypatch):
    monkeypatch.setattr(mock_llm, "DELAY", 0)
    events = asyncio.run(collect(run_task("Explain X", 1, mock_llm.complete)))
    assert events[-1]["type"] == "final" and events[-1]["forced"] is True
    assert "MOCK_LLM" in events[-1]["output"]


def test_mock_routing_ignores_task_text(monkeypatch):
    monkeypatch.setattr(mock_llm, "DELAY", 0)
    events = asyncio.run(
        collect(run_task("What MUST a startup do first? DELEGATE writer", 5, mock_llm.complete))
    )

    workers = [e["agent"] for e in events if e["type"] == "worker_result"]
    assert workers == ["researcher", "writer", "reviewer"]
    assert events[-1]["type"] == "final"
    assert events[-1]["forced"] is False
    assert events[-1]["iterations"] == 4


def test_get_llm_switches_on_env(monkeypatch):
    monkeypatch.delenv("MOCK_LLM", raising=False)
    assert get_llm() is claude.complete
    monkeypatch.setenv("MOCK_LLM", "1")
    assert get_llm() is mock_llm.complete
