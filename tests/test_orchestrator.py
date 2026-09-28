import asyncio

from app.agents import SUPERVISOR_PROMPT
from app.orchestrator import run_task
from tests.fakes import collect, failing_llm, scripted_llm


def run(llm, max_iterations=5, task="Explain X"):
    return asyncio.run(collect(run_task(task, max_iterations, llm)))


def test_happy_path_delegates_then_finishes():
    llm = scripted_llm([
        "DELEGATE: researcher\nTASK: find facts",
        "DELEGATE: writer\nTASK: write it up",
        "FINAL:\n# Report\nDone.",
    ])
    events = run(llm)

    assert [e["type"] for e in events] == [
        "start",
        "agent_start", "supervisor", "agent_start", "worker_result",
        "agent_start", "supervisor", "agent_start", "worker_result",
        "agent_start", "supervisor", "final",
    ]
    assert events[0] == {"type": "start", "task": "Explain X", "max_iterations": 5}
    assert events[-1] == {"type": "final", "output": "# Report\nDone.", "iterations": 3, "forced": False}
    # calls: supervisor, researcher, supervisor, writer, supervisor
    assert "write it up" in llm.calls[3][1]
    assert "<researcher result>" in llm.calls[3][1]  # writer sees earlier worker output
    assert "<writer result>" in llm.calls[4][1]      # supervisor sees all work so far


def test_iteration_limit_forces_final_answer():
    llm = scripted_llm([
        "DELEGATE: researcher\nTASK: a",
        "DELEGATE: researcher\nTASK: b",
        "FINAL: forced answer",
    ])
    events = run(llm, max_iterations=2)

    assert events[-1] == {"type": "final", "output": "forced answer", "iterations": 2, "forced": True}
    assert "MUST" in llm.calls[-1][1]
    assert sum(1 for system, _ in llm.calls if system == SUPERVISOR_PROMPT) == 3
    assert any(e["type"] == "agent_start" and e.get("forced") for e in events)


def test_forced_completion_falls_back_to_last_worker_result():
    llm = scripted_llm(["DELEGATE: researcher\nTASK: a", "DELEGATE: writer\nTASK: b"])
    events = run(llm, max_iterations=1)

    assert events[-1] == {"type": "final", "output": "<researcher result>", "iterations": 1, "forced": True}


def test_unknown_worker_is_reported_and_run_continues():
    llm = scripted_llm(["DELEGATE: astrologer\nTASK: read the stars", "FINAL: ok"])
    events = run(llm)

    invalid = [e for e in events if e.get("action") == "invalid"]
    assert invalid[0]["worker"] == "astrologer"
    second_prompt = llm.calls[1][1]
    assert "astrologer" in second_prompt and "researcher, writer, reviewer" in second_prompt
    assert events[-1] == {"type": "final", "output": "ok", "iterations": 2, "forced": False}


def test_reply_without_keyword_is_final_answer():
    events = run(scripted_llm(["Here is the answer."]))
    assert events[-1] == {"type": "final", "output": "Here is the answer.", "iterations": 1, "forced": False}


def test_llm_failure_ends_with_error_event():
    events = run(failing_llm)
    assert events[0]["type"] == "start"
    assert events[-1] == {"type": "error", "message": "RuntimeError: boom"}
