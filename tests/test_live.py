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
