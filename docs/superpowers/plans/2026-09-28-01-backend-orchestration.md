# Backend Orchestration Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A FastAPI service where a Supervisor agent (Claude) delegates a research task to Researcher / Writer / Reviewer workers using the DELEGATE/TASK and FINAL formats, with an iteration limit, a `POST /run` JSON endpoint, a `POST /run/stream` SSE endpoint, and `GET /health`.

**Architecture:** `app/llm.py` is the only file that talks to Claude (`complete(system, prompt) -> str`). `app/agents.py` holds the prompts and the decision parser. `app/orchestrator.py` is an async generator that runs the supervisor loop and yields plain-dict events; it takes the LLM as a parameter so tests inject a scripted fake. `main.py` exposes the generator as JSON (`/run`) and as Server-Sent Events (`/run/stream`).

**Tech Stack:** Python 3.12, FastAPI, uvicorn, `anthropic` Python SDK (async client), pytest + httpx (FastAPI TestClient). Package management with `uv`.

**Spec:** `requirements.md` (repo root)

## Global Constraints

- Run command must be exactly `uvicorn main:app --reload` from the repo root, so `main.py` lives at the root and exposes `app`.
- `POST /run` accepts `{"task": "...", "max_iterations": 5}`. `max_iterations` is optional (default 5) and limited to 1–10. `task` is 1–4000 chars after trimming whitespace.
- The supervisor delegates with `DELEGATE: <worker>` + `TASK: <instructions>` and finishes with `FINAL:` + answer.
- At least 2 workers. This plan builds 3: `researcher`, `writer`, `reviewer`.
- Iteration limit with forced completion. One supervisor decision is one iteration.
- Health check endpoint: `GET /health` returns `{"status": "ok"}`.
- Runtime dependencies are limited to `fastapi`, `uvicorn[standard]`, and `anthropic`. Dev dependencies are limited to `pytest` and `httpx`. Add nothing else.
- Claude model: `claude-opus-5` by default, overridable with the `ANTHROPIC_MODEL` env var. Use adaptive thinking and `output_config.effort` (default `medium`, overridable with `ANTHROPIC_EFFORT`). Enable the server-side refusal fallback: beta `server-side-fallback-2026-07-01` + `fallbacks: "default"`. Never hardcode an API key; the SDK reads `ANTHROPIC_API_KEY`.
- Work directly on `main`. Do not create branches or worktrees. Every commit ends with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run all commands from the repo root: `/Users/harielgiacomuzzi/Development/MultiAgentSupervisor`.

## Review Focus

1. **Supervisor drifts from the exact format** (`**DELEGATE:** Writer`, lowercase `final:`, a preamble sentence before the keyword). It should still be parsed correctly and should not be mistaken for a final answer. Pinned in Task 2.
2. **Supervisor never says FINAL.** At the iteration limit the user should still get a useful answer: the forced supervisor answer, or the last worker result if the supervisor still tries to delegate. Pinned in Task 3.
3. **Claude call fails** (missing key, network error, 5xx, refusal). `/run` should return a 502 with a readable message, and the stream should end with an `error` event instead of hanging. Pinned in Tasks 1, 3 and 4.
4. **Supervisor delegates to a worker that doesn't exist** (`DELEGATE: astrologer`). The run should continue, and the supervisor should be told which workers are valid. Pinned in Task 3.
5. **Bad request input** (empty or whitespace-only task, `max_iterations` of 0 or 11). The API should return 422 without calling Claude. Pinned in Task 4.

---

### Task 1: Project scaffold + Claude wrapper

**Files:**
- Create: `.gitignore`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`
- Create: `app/__init__.py` (empty), `app/llm.py`
- Create: `tests/__init__.py` (empty), `tests/test_llm.py`
- Also commit: the existing `requirements.md` and `docs/superpowers/plans/*.md`

**Interfaces:**
- Produces: `app.llm.complete(system: str, prompt: str) -> str` (async), `app.llm.LLMError(Exception)`, module constants `app.llm.MODEL: str` and `app.llm.EFFORT: str`, and the module-level `app.llm._client` (lazily created `anthropic.AsyncAnthropic`, which tests replace).

- [ ] **Step 1: Create the environment and pin dependencies**

```bash
uv venv --python 3.12 .venv
uv pip install fastapi "uvicorn[standard]" anthropic pytest httpx
uv pip freeze | grep -iE '^(fastapi|uvicorn|anthropic|pytest|httpx)=='
```

Write `requirements.txt` using the installed versions printed above as lower bounds. For example, if freeze printed `fastapi==0.118.0`, write `fastapi>=0.118.0`:

```text
fastapi>=<installed fastapi version>
uvicorn[standard]>=<installed uvicorn version>
anthropic>=<installed anthropic version>
```

`requirements-dev.txt`:

```text
-r requirements.txt
pytest>=<installed pytest version>
httpx>=<installed httpx version>
```

`pytest.ini`:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

`.gitignore`:

```text
.venv/
__pycache__/
.pytest_cache/
.env
```

Create empty `app/__init__.py` and `tests/__init__.py`.

- [ ] **Step 2: Write the failing tests** — `tests/test_llm.py`

```python
import asyncio
from types import SimpleNamespace

import pytest

from app import llm


class StubMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def install_stub(monkeypatch, content, stop_reason="end_turn"):
    messages = StubMessages(SimpleNamespace(content=content, stop_reason=stop_reason))
    monkeypatch.setattr(llm, "_client", SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def test_complete_joins_text_blocks_and_skips_thinking(monkeypatch):
    stub = install_stub(monkeypatch, [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="Hello "),
        SimpleNamespace(type="text", text="world"),
    ])

    assert asyncio.run(llm.complete("sys", "hi")) == "Hello world"
    assert stub.kwargs["model"] == llm.MODEL
    assert stub.kwargs["system"] == "sys"
    assert stub.kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert stub.kwargs["thinking"] == {"type": "adaptive"}
    assert stub.kwargs["output_config"] == {"effort": llm.EFFORT}
    assert stub.kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert stub.kwargs["extra_body"] == {"fallbacks": "default"}


def test_complete_raises_on_refusal(monkeypatch):
    install_stub(monkeypatch, [], stop_reason="refusal")
    with pytest.raises(llm.LLMError, match="declined"):
        asyncio.run(llm.complete("sys", "hi"))


def test_complete_raises_on_empty_text(monkeypatch):
    install_stub(monkeypatch, [SimpleNamespace(type="thinking", thinking="")])
    with pytest.raises(llm.LLMError, match="empty"):
        asyncio.run(llm.complete("sys", "hi"))
```

- [ ] **Step 3: Run and verify the tests fail**

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: FAIL/ERROR with `ImportError: cannot import name 'llm' from 'app'`

- [ ] **Step 4: Implement** — `app/llm.py`

```python
"""The only module that talks to Claude. Every agent calls complete()."""
import os

import anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
EFFORT = os.environ.get("ANTHROPIC_EFFORT", "medium")

_client: anthropic.AsyncAnthropic | None = None


class LLMError(Exception):
    """Claude answered, but not with usable text."""


def _get_client() -> anthropic.AsyncAnthropic:
    # Lazy so importing the app never needs credentials (tests, MOCK_LLM mode).
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def complete(system: str, prompt: str) -> str:
    response = await _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},  # re-run refused requests on Anthropic's recommended model
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise LLMError("Claude returned an empty response.")
    return text
```

- [ ] **Step 5: Run and verify the tests pass**

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add .gitignore requirements.txt requirements-dev.txt pytest.ini app/__init__.py app/llm.py tests/__init__.py tests/test_llm.py requirements.md docs/superpowers/plans
git commit -m "feat: scaffold project and add Claude completion wrapper" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Agent prompts + supervisor decision parser

**Files:**
- Create: `app/agents.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SUPERVISOR_PROMPT: str`
  - `WORKER_PROMPTS: dict[str, str]` with keys `"researcher"`, `"writer"`, `"reviewer"` (in that order)
  - `@dataclass Decision(action: str, worker: str = "", task: str = "", output: str = "")`, where `action` is `"delegate"` or `"final"`
  - `parse_decision(text: str) -> Decision`
  - `build_supervisor_prompt(task: str, history: list[str], iteration: int, max_iterations: int, force: bool = False) -> str`. Includes every `history` entry verbatim. When `force=True` it contains the word `MUST`.
  - `build_worker_prompt(task: str, results: list[str]) -> str`. Includes the task and every entry in `results`.

- [ ] **Step 1: Write the failing tests** — `tests/test_agents.py`

```python
from app.agents import (
    WORKER_PROMPTS,
    Decision,
    build_supervisor_prompt,
    build_worker_prompt,
    parse_decision,
)


def test_has_three_workers():
    assert list(WORKER_PROMPTS) == ["researcher", "writer", "reviewer"]


def test_parses_delegate_with_multiline_task():
    d = parse_decision("DELEGATE: researcher\nTASK: Find the history of X.\nInclude dates.")
    assert d == Decision("delegate", worker="researcher", task="Find the history of X.\nInclude dates.")


def test_delegate_tolerates_case_bold_and_preamble():
    d = parse_decision("Let me start with research.\n**DELEGATE:** Writer\n**TASK:** Draft it")
    assert d == Decision("delegate", worker="writer", task="Draft it")


def test_parses_final_multiline_markdown():
    d = parse_decision("FINAL:\n# Title\n\nBody with **bold**.")
    assert d == Decision("final", output="# Title\n\nBody with **bold**.")


def test_lowercase_final_is_accepted():
    assert parse_decision("final: done").output == "done"


def test_earliest_keyword_wins():
    d = parse_decision("FINAL: answer\nDELEGATE: writer\nTASK: y")
    assert d.action == "final"
    d = parse_decision("DELEGATE: writer\nTASK: polish it\nFINAL: later")
    assert d.action == "delegate"


def test_keyword_mid_sentence_is_not_a_decision():
    d = parse_decision("DELEGATE: writer\nTASK: write the final: summary section")
    assert d == Decision("delegate", worker="writer", task="write the final: summary section")


def test_unparseable_text_becomes_final_answer():
    assert parse_decision("  Just an answer.  ") == Decision("final", output="Just an answer.")


def test_supervisor_prompt_includes_history_and_force_note():
    prompt = build_supervisor_prompt("Explain X", ["[Iteration 1] DELEGATE researcher: a\nResult:\nfacts"], 2, 5)
    assert "Explain X" in prompt and "facts" in prompt and "Iteration 2 of 5" in prompt
    assert "MUST" not in prompt
    assert "MUST" in build_supervisor_prompt("Explain X", [], 5, 5, force=True)


def test_worker_prompt_includes_task_and_results():
    prompt = build_worker_prompt("Write it", ["### researcher (iteration 1)\nfacts"])
    assert "Write it" in prompt and "facts" in prompt
    assert "(none yet)" in build_worker_prompt("Research it", [])
```

- [ ] **Step 2: Run and verify the tests fail**

Run: `.venv/bin/pytest tests/test_agents.py -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'app.agents'`

- [ ] **Step 3: Implement** — `app/agents.py`

```python
"""Agent system prompts and the supervisor's DELEGATE/TASK/FINAL protocol."""
import re
from dataclasses import dataclass

SUPERVISOR_PROMPT = """You are the Supervisor of a small research team. You never do the research or writing yourself; you coordinate workers and synthesize their results.

Workers:
- researcher: finds and summarizes facts, data, definitions and key perspectives on a topic.
- writer: turns research notes into polished, well-structured Markdown prose.
- reviewer: critiques a draft for accuracy, clarity and gaps, and lists concrete fixes.

Each turn, reply in EXACTLY one of these two formats and nothing else.

To delegate to one worker:
DELEGATE: <researcher|writer|reviewer>
TASK: <specific, self-contained instructions for that worker>

To finish:
FINAL:
<the complete final answer for the user, in Markdown>

Typical flow: researcher, then writer, optionally reviewer (then writer again if the reviewer asked for changes), then FINAL. Your FINAL answer must be the full polished result that synthesizes the workers' output, not a pointer to it."""

WORKER_PROMPTS = {
    "researcher": (
        "You are the Researcher on a small research team. Given a task and any context from teammates, "
        "gather the relevant facts, figures, definitions, key arguments and notable perspectives. "
        "Return concise research notes as Markdown bullet points grouped under short headings. "
        "Flag anything uncertain or likely out of date. Do not write polished prose; that is the Writer's job."
    ),
    "writer": (
        "You are the Writer on a small research team. Turn the research notes in the context into polished, "
        "well-structured Markdown: a clear title, a short introduction, logically ordered sections and a brief "
        "conclusion. Stay faithful to the research and do not invent facts. Apply any reviewer feedback present "
        "in the context. Follow any audience, length or style instructions in the task."
    ),
    "reviewer": (
        "You are the Reviewer on a small research team. Critically review the latest draft in the context for "
        "factual accuracy, clarity, structure and gaps relative to the task. Start with a one-line verdict, "
        "APPROVED or NEEDS CHANGES, then give a numbered list of specific, actionable fixes."
    ),
}


@dataclass
class Decision:
    action: str  # "delegate" | "final"
    worker: str = ""
    task: str = ""
    output: str = ""


# Models sometimes bold the keywords: **DELEGATE:** / **DELEGATE**: -> DELEGATE:
_BOLD = re.compile(r"\*\*\s*(DELEGATE|TASK|FINAL)\s*(?:\*\*)?\s*:\s*(?:\*\*)?", re.I)
_DELEGATE = re.compile(r"^[ \t]*DELEGATE:[ \t]*(\w+).*?^[ \t]*TASK:[ \t]*(.+)", re.I | re.M | re.S)
_FINAL = re.compile(r"^[ \t]*FINAL:[ \t]*(.*)", re.I | re.M | re.S)


def parse_decision(text: str) -> Decision:
    text = _BOLD.sub(r"\1: ", text)
    delegate, final = _DELEGATE.search(text), _FINAL.search(text)
    if delegate and (not final or delegate.start() < final.start()):
        return Decision("delegate", worker=delegate.group(1).lower(), task=delegate.group(2).strip())
    if final and final.group(1).strip():
        return Decision("final", output=final.group(1).strip())
    # No protocol keyword: treat the whole reply as the answer rather than failing the run.
    return Decision("final", output=text.strip())


def build_supervisor_prompt(task: str, history: list[str], iteration: int, max_iterations: int, force: bool = False) -> str:
    work = "\n\n".join(history) or "(nothing yet)"
    prompt = f"User task:\n{task}\n\nIteration {iteration} of {max_iterations}.\n\nWork so far:\n{work}\n\n"
    if force:
        return prompt + (
            "You have reached the iteration limit. You MUST reply now with FINAL: followed by the best complete "
            "answer you can synthesize from the work so far. Do not delegate."
        )
    return prompt + "Reply with DELEGATE/TASK or FINAL."


def build_worker_prompt(task: str, results: list[str]) -> str:
    context = "\n\n".join(results) or "(none yet)"
    return f"## Task\n{task}\n\n## Context from teammates\n{context}"
```

Note: the `TASK:` part of `_DELEGATE` is anchored to a line start (`^`), so `TASK:` must begin its own line. That is how `test_delegate_tolerates_case_bold_and_preamble` and `test_keyword_mid_sentence_is_not_a_decision` expect it.

- [ ] **Step 4: Run and verify the tests pass**

Run: `.venv/bin/pytest tests/test_agents.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add app/agents.py tests/test_agents.py
git commit -m "feat: add agent prompts and DELEGATE/FINAL decision parser" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Supervisor orchestration loop

**Files:**
- Create: `app/orchestrator.py`
- Create: `tests/fakes.py` (test helpers shared with Task 4 and Plan 2)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: everything Task 2 produces.
- Produces:
  - `LLM = Callable[[str, str], Awaitable[str]]` (the type of `app.llm.complete`)
  - `run_task(task: str, max_iterations: int, llm: LLM) -> AsyncIterator[dict]`. Event dicts, in order:
    - `{"type": "start", "task", "max_iterations"}` — always first
    - `{"type": "agent_start", "agent", "iteration"}` — plus `"forced": True` on the forced supervisor call
    - `{"type": "supervisor", "iteration", "action": "delegate"|"invalid"|"final", "raw", ...}` — `delegate`/`invalid` also carry `"worker"` and `"task"`; the forced one also carries `"forced": True`
    - `{"type": "worker_result", "agent", "iteration", "task", "result"}`
    - Always last: `{"type": "final", "output", "iterations", "forced"}` or `{"type": "error", "message"}`
  - Supervisor history entry format, relied on by Plan 2's mock LLM: `"[Iteration {i}] DELEGATE {worker}: {task}\nResult:\n{result}"`
  - `tests/fakes.py`: `scripted_llm(supervisor_replies: list[str]) -> LLM`, which records `.calls: list[tuple[str, str]]`; workers reply `"<{worker} result>"`. Also `failing_llm` (always raises `RuntimeError("boom")`) and `collect(agen) -> list` (async).

- [ ] **Step 1: Write the test helpers** — `tests/fakes.py`

```python
from app.agents import SUPERVISOR_PROMPT, WORKER_PROMPTS


def scripted_llm(supervisor_replies):
    """Fake LLM: supervisor returns the scripted replies in order; each worker returns '<name result>'."""
    replies = iter(supervisor_replies)
    calls = []

    async def llm(system, prompt):
        calls.append((system, prompt))
        if system == SUPERVISOR_PROMPT:
            return next(replies)
        worker = next(name for name, p in WORKER_PROMPTS.items() if p == system)
        return f"<{worker} result>"

    llm.calls = calls
    return llm


async def failing_llm(system, prompt):
    raise RuntimeError("boom")


async def collect(agen):
    return [event async for event in agen]
```

- [ ] **Step 2: Write the failing tests** — `tests/test_orchestrator.py`

```python
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
```

- [ ] **Step 3: Run and verify the tests fail**

Run: `.venv/bin/pytest tests/test_orchestrator.py -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'app.orchestrator'`

- [ ] **Step 4: Implement** — `app/orchestrator.py`

```python
"""Supervisor loop: ask the supervisor what to do, run the chosen worker, repeat, and stream events."""
from collections.abc import AsyncIterator, Awaitable, Callable

from app.agents import (
    SUPERVISOR_PROMPT,
    WORKER_PROMPTS,
    build_supervisor_prompt,
    build_worker_prompt,
    parse_decision,
)

LLM = Callable[[str, str], Awaitable[str]]


async def run_task(task: str, max_iterations: int, llm: LLM) -> AsyncIterator[dict]:
    """Yield event dicts for one run. The last event is always `final` or `error`."""
    yield {"type": "start", "task": task, "max_iterations": max_iterations}
    try:
        async for event in _loop(task, max_iterations, llm):
            yield event
    except Exception as exc:  # any Claude/network failure becomes an event instead of a hung stream
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}


async def _loop(task: str, max_iterations: int, llm: LLM) -> AsyncIterator[dict]:
    history: list[str] = []  # what the supervisor sees
    results: list[str] = []  # what workers see as context
    last_result = ""

    for i in range(1, max_iterations + 1):
        yield {"type": "agent_start", "agent": "supervisor", "iteration": i}
        raw = await llm(SUPERVISOR_PROMPT, build_supervisor_prompt(task, history, i, max_iterations))
        decision = parse_decision(raw)

        if decision.action == "final":
            yield {"type": "supervisor", "iteration": i, "action": "final", "raw": raw}
            yield {"type": "final", "output": decision.output, "iterations": i, "forced": False}
            return

        if decision.worker not in WORKER_PROMPTS:
            yield {"type": "supervisor", "iteration": i, "action": "invalid",
                   "worker": decision.worker, "task": decision.task, "raw": raw}
            history.append(f"[Iteration {i}] Invalid delegation to '{decision.worker}'. "
                           f"Valid workers: {', '.join(WORKER_PROMPTS)}.")
            continue

        yield {"type": "supervisor", "iteration": i, "action": "delegate",
               "worker": decision.worker, "task": decision.task, "raw": raw}
        yield {"type": "agent_start", "agent": decision.worker, "iteration": i}
        last_result = await llm(WORKER_PROMPTS[decision.worker], build_worker_prompt(decision.task, results))
        results.append(f"### {decision.worker} (iteration {i})\n{last_result}")
        history.append(f"[Iteration {i}] DELEGATE {decision.worker}: {decision.task}\nResult:\n{last_result}")
        yield {"type": "worker_result", "agent": decision.worker, "iteration": i,
               "task": decision.task, "result": last_result}

    # Iteration limit reached without FINAL: force one last synthesis.
    yield {"type": "agent_start", "agent": "supervisor", "iteration": max_iterations, "forced": True}
    raw = await llm(SUPERVISOR_PROMPT, build_supervisor_prompt(task, history, max_iterations, max_iterations, force=True))
    decision = parse_decision(raw)
    output = decision.output if decision.action == "final" else (last_result or raw)
    yield {"type": "supervisor", "iteration": max_iterations, "action": "final", "raw": raw, "forced": True}
    yield {"type": "final", "output": output, "iterations": max_iterations, "forced": True}
```

- [ ] **Step 5: Run and verify the tests pass**

Run: `.venv/bin/pytest tests/test_orchestrator.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add app/orchestrator.py tests/fakes.py tests/test_orchestrator.py
git commit -m "feat: add supervisor orchestration loop with forced completion" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: FastAPI app — `/health`, `/run`, `/run/stream`

**Files:**
- Create: `main.py`
- Test: `tests/test_api.py`, `tests/test_live.py`

**Interfaces:**
- Consumes: `run_task` and `LLM` from Task 3, `app.llm.complete` from Task 1, and `tests/fakes.py`.
- Produces:
  - `main.app` (FastAPI) and `main.get_llm() -> LLM`, the dependency that tests override
  - `RunRequest` pydantic model
  - `GET /health` returns `{"status": "ok"}`
  - `POST /run` returns `{"task", "final_output", "iterations", "forced", "steps": [events...]}`; on an LLM failure it returns 502 `{"detail": "<message>"}`
  - `POST /run/stream` returns a `text/event-stream` response with one `data: <json event>\n\n` per event
  - Plan 2 adds a static-files mount and a `/` route to this file, and adds `MOCK_LLM` handling to `get_llm`.

- [ ] **Step 1: Write the failing tests** — `tests/test_api.py`

```python
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
```

`tests/test_live.py` (real end-to-end check against Claude; it skips when no key is set):

```python
import asyncio
import os

import pytest

from app import llm
from app.orchestrator import run_task
from tests.fakes import collect

pytestmark = pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")


def test_live_end_to_end():
    task = "In about 100 words, explain the supervisor pattern in multi-agent LLM systems."
    events = asyncio.run(collect(run_task(task, 4, llm.complete)))
    assert events[-1]["type"] == "final", events[-1]
    assert events[-1]["output"].strip()
    assert any(e["type"] == "worker_result" for e in events)
```

- [ ] **Step 2: Run and verify the tests fail**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Implement** — `main.py`

```python
"""HTTP API for the multi-agent research assistant. Run: uvicorn main:app --reload"""
import json
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from app import llm as claude
from app.orchestrator import LLM, run_task

app = FastAPI(title="Multi-Agent Supervisor")


class RunRequest(BaseModel):
    task: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    max_iterations: int = Field(default=5, ge=1, le=10)


def get_llm() -> LLM:
    return claude.complete


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
async def run(req: RunRequest, llm: LLM = Depends(get_llm)):
    steps = [event async for event in run_task(req.task, req.max_iterations, llm)]
    last = steps[-1]
    if last["type"] == "error":
        raise HTTPException(status_code=502, detail=last["message"])
    return {
        "task": req.task,
        "final_output": last["output"],
        "iterations": last["iterations"],
        "forced": last["forced"],
        "steps": steps,
    }


@app.post("/run/stream")
async def run_stream(req: RunRequest, llm: LLM = Depends(get_llm)):
    async def events():
        async for event in run_task(req.task, req.max_iterations, llm):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Run and verify the tests pass**

Run: `.venv/bin/pytest -v`
Expected: all tests pass (the 3 + 10 + 6 tests from Tasks 1–3, 12 from `test_api.py`), and `test_live_end_to_end` is SKIPPED unless `ANTHROPIC_API_KEY` is set. If the key is set, the live test must PASS.

- [ ] **Step 5: Smoke-test the real server**

```bash
.venv/bin/uvicorn main:app --port 8000 &
sleep 2
curl -s localhost:8000/health
curl -s -X POST localhost:8000/run -H 'Content-Type: application/json' -d '{"task": ""}' -o /dev/null -w '%{http_code}\n'
kill %1
```

Expected: `{"status":"ok"}`, then `422`.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_api.py tests/test_live.py
git commit -m "feat: expose /health, /run and /run/stream endpoints" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
