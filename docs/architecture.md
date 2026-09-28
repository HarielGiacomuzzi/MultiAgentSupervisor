# Architecture

## Components

| File | Responsibility |
|---|---|
| `app/llm.py` | `complete(system, prompt) -> str`. One Claude Messages API call (`claude-opus-5`, adaptive thinking, `effort` from env, server-side refusal fallback). Raises `LLMError` on refusal or empty output. |
| `app/agents.py` | `SUPERVISOR_PROMPT`, `WORKER_PROMPTS` (researcher, writer, reviewer), `parse_decision()`, and the prompt builders. |
| `app/orchestrator.py` | `run_task(task, max_iterations, llm)`: an async generator that runs the supervisor loop and yields events. The LLM is injected, so tests and mock mode swap it out. |
| `app/mock_llm.py` | Deterministic offline LLM with the same signature as `complete`. |
| `main.py` | FastAPI app. `/run` collects the events into JSON; `/run/stream` forwards them as SSE; `/` serves the UI. |
| `static/` | Vanilla JS UI. `sse.js` parses the stream; `app.js` renders the feed, conversation and final output (Markdown via `marked`, sanitized by `DOMPurify`). |

## Supervisor protocol

The supervisor replies with exactly one of:

```
DELEGATE: <researcher|writer|reviewer>
TASK: <instructions>
```

```
FINAL:
<markdown answer>
```

Parsing is tolerant. Keywords are case-insensitive, `**bold**` markers are stripped, and a preamble is allowed. Keywords only count at the start of a line, and the earliest keyword wins. A reply with no keyword is treated as the final answer.

## Loop

1. Iteration `i` (1..`max_iterations`): ask the supervisor, giving it the task plus the history of every delegation and result.
2. `FINAL` → emit `final` and stop.
3. Unknown worker → emit `supervisor` with `action: "invalid"`, add a note listing the valid workers to the history, and continue.
4. Valid worker → run it with the supervisor's `TASK` plus all earlier worker results as context, then record the result.
5. Limit reached → one extra supervisor call that is told it MUST answer with `FINAL`. If it still delegates, the last worker result becomes the output. The `final` event has `forced: true`.

Any exception (auth, network, refusal) ends the run with an `error` event.

## Event contract

| `type` | Fields |
|---|---|
| `start` | `task`, `max_iterations` |
| `agent_start` | `agent`, `iteration`, optional `forced: true` |
| `supervisor` | `iteration`, `action` (`delegate` / `invalid` / `final`), `raw`, plus `worker` and `task` for delegate/invalid, and optional `forced: true` |
| `worker_result` | `agent`, `iteration`, `task`, `result` |
| `final` (last) | `output`, `iterations`, `forced` |
| `error` (last) | `message` |

`POST /run` returns these as `steps`. `POST /run/stream` sends each one as `data: <json>\n\n`.

## Design choices

- **SSE over fetch** instead of WebSockets: it's one-way, works through proxies, and needs no extra dependency. `EventSource` is GET-only, so the client reads the POST body stream itself.
- **Sequential workers**: the spec's flow is inherently ordered (research, then write, then review). Parallel workers are listed as an extension.
- **Injected LLM callable**: this keeps the orchestrator pure and testable. There is no mocking of the SDK outside `tests/test_llm.py`.
