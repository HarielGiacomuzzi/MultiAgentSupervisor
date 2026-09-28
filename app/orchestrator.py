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
