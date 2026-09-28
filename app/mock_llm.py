"""Offline stand-in for app.llm.complete. Enable with MOCK_LLM=1 for demos and UI work without an API key."""
import asyncio
import os

from app.agents import SUPERVISOR_PROMPT, WORKER_PROMPTS

DELAY = float(os.environ.get("MOCK_LLM_DELAY", "0.8"))

_WORKER_OUTPUT = {
    "researcher": "## Key facts\n- Mock fact one about the topic.\n- Mock fact two, with a figure: 42%.\n\n## Open questions\n- Anything uncertain would be flagged here.",
    "writer": "# Mock Report\n\nA short introduction built from the research notes.\n\n## Findings\nMock fact one and mock fact two, woven into prose.\n\n## Conclusion\nA brief wrap-up.",
    "reviewer": "APPROVED\n\n1. Optional: add a source for the 42% figure.",
}

_FINAL = (
    "FINAL:\n# Mock Report\n\n> Generated in **MOCK_LLM** mode: no model was called.\n\n"
    "## Findings\n- Mock fact one about the topic.\n- Mock fact two, with a figure: 42%.\n\n"
    "## Conclusion\nThe supervisor coordinated research, writing and review to produce this answer."
)


async def complete(system: str, prompt: str) -> str:
    await asyncio.sleep(DELAY)
    if system != SUPERVISOR_PROMPT:
        worker = next(name for name, p in WORKER_PROMPTS.items() if p == system)
        return _WORKER_OUTPUT[worker]
    # The supervisor prompt carries the history ("DELEGATE <worker>: ...") and says MUST when
    # forced. Only look past "Work so far:" so the user's task text can't trigger routing.
    work_so_far = prompt.partition("Work so far:")[2]
    if "MUST" in work_so_far or "DELEGATE reviewer" in work_so_far:
        return _FINAL
    if "DELEGATE writer" in work_so_far:
        return "DELEGATE: reviewer\nTASK: Review the draft for accuracy, clarity and gaps."
    if "DELEGATE researcher" in work_so_far:
        return "DELEGATE: writer\nTASK: Turn the research notes into a short, well-structured report."
    return "DELEGATE: researcher\nTASK: Gather the key facts, figures and perspectives for the user's task."
