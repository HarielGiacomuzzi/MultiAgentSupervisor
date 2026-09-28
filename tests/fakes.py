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
