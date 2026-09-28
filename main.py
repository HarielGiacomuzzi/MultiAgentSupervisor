"""HTTP API for the multi-agent research assistant. Run: uvicorn main:app --reload"""
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from app import llm as claude, mock_llm
from app.orchestrator import LLM, run_task

app = FastAPI(title="Multi-Agent Supervisor")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


class RunRequest(BaseModel):
    task: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    max_iterations: int = Field(default=5, ge=1, le=10)


def get_llm() -> LLM:
    return mock_llm.complete if os.environ.get("MOCK_LLM") == "1" else claude.complete


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
