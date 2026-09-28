# Web Frontend Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A chat-like, responsive web UI served by the FastAPI app. It streams a run live: which agent is working, a conversation of Supervisor decisions and Worker results, and a formatted final-output panel. It also adds an offline `MOCK_LLM=1` mode, so the UI can be demoed and verified without an API key.

**Architecture:** No build step. `static/index.html`, `static/styles.css`, and `static/app.js` (an ES module) are served by FastAPI's `StaticFiles`, with `/` returning `index.html`. `app.js` POSTs to `/run/stream` and reads the body with `fetch` + a `ReadableStream`; `EventSource` only supports GET, so it can't be used. The SSE parsing lives in `static/sse.js`, a pure function unit-tested with `node --test`. Markdown is rendered with `marked` and sanitized with `DOMPurify`, both loaded from jsdelivr.

**Tech Stack:** Vanilla HTML/CSS/JS (ES modules), `marked@12.0.2`, `dompurify@3.1.6`, Node's built-in test runner (Node ≥ 20) for `sse.js`, pytest for the Python pieces.

**Spec:** `requirements.md` (repo root). Builds on Plan 1: `docs/superpowers/plans/2026-09-28-01-backend-orchestration.md`.

## Global Constraints

- Frontend requirements from the spec: a chat-like interface to submit research tasks; a real-time agent activity feed showing which agent is working; a conversation history showing Supervisor decisions and Worker results; a final output panel with the formatted result; a responsive design.
- No frontend build tooling, `package.json` or npm dependencies. The only third-party browser code is `marked@12.0.2` and `dompurify@3.1.6` from `https://cdn.jsdelivr.net/npm/`.
- Model output must never reach `innerHTML` without `DOMPurify.sanitize`. If the CDN scripts fail to load, show the text escaped inside a `<pre>`.
- No new Python dependencies.
- The Plan 1 constraints still apply: run with `uvicorn main:app --reload`; work on `main`; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; run commands from the repo root using `.venv/bin/...`.
- Before writing the UI in Task 3, the implementer loads the `frontend-design:frontend-design` skill. The CSS below is the baseline: polish is welcome, but the element IDs, `data-agent` / `data-state` attributes, and the class names that `app.js` uses must not change.

## Review Focus

1. **SSE event split across network chunks.** A JSON event cut in half between two reads should be reassembled, not thrown away or turned into an exception. Pinned in Task 1.
2. **Model output containing HTML or `<script>`.** It should render as inert text or markup and never execute. Guaranteed by `md()` always sanitizing. Verified manually in Task 3, Step 6.
3. **Stream ends without `final` or `error`** (server restart, proxy timeout). The UI should show an error and re-enable the Run button, not spin forever. Covered by `app.js` and verified manually in Task 3, Step 6.
4. **422 from bad input** (for example `max_iterations` edited to 50). The UI should show the validation message, not `[object Object]`. Covered by `formatDetail` and verified manually in Task 3, Step 6.
5. **Double submit while a run is in progress.** It should be ignored: the button is disabled and the handler checks `running`. Verified manually in Task 3, Step 6.

---

### Task 1: SSE stream parser

**Files:**
- Create: `static/sse.js`
- Test: `tests/js/sse.test.mjs`

**Interfaces:**
- Produces: `export function parseSSE(buffer: string): [events: object[], remainder: string]`. It splits on blank lines, JSON-parses the `data:` payload of each complete block, and returns the unfinished tail as `remainder`.

- [ ] **Step 1: Write the failing test** — `tests/js/sse.test.mjs`

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseSSE } from "../../static/sse.js";

test("parses complete events and keeps the partial remainder", () => {
  const [events, rest] = parseSSE('data: {"type":"start"}\n\ndata: {"type":"fin');
  assert.deepEqual(events, [{ type: "start" }]);
  assert.equal(rest, 'data: {"type":"fin');
});

test("reassembles an event split across chunks", () => {
  let buf = "";
  const got = [];
  for (const chunk of ['data: {"type":"fi', 'nal","output":"a\\nb"}\n', "\n"]) {
    let events;
    [events, buf] = parseSSE(buf + chunk);
    got.push(...events);
  }
  assert.deepEqual(got, [{ type: "final", output: "a\nb" }]);
  assert.equal(buf, "");
});

test("ignores blocks without data lines", () => {
  const [events, rest] = parseSSE(": keepalive\n\n");
  assert.deepEqual(events, []);
  assert.equal(rest, "");
});
```

- [ ] **Step 2: Run and verify the test fails**

Run: `node --test tests/js/*.test.mjs`
Expected: FAIL with `Cannot find module '.../static/sse.js'` (ERR_MODULE_NOT_FOUND)

- [ ] **Step 3: Implement** — `static/sse.js`

```js
// Split a text/event-stream buffer into complete events.
// Returns [events, remainder]; feed remainder + the next chunk into the next call.
export function parseSSE(buffer) {
  const blocks = buffer.split("\n\n");
  const remainder = blocks.pop();
  const events = [];
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (data) events.push(JSON.parse(data));
  }
  return [events, remainder];
}
```

- [ ] **Step 4: Run and verify the test passes**

Run: `node --test tests/js/*.test.mjs`
Expected: 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add static/sse.js tests/js/sse.test.mjs
git commit -m "feat: add chunk-safe SSE parser for the web client" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Offline mock LLM mode (`MOCK_LLM=1`)

**Files:**
- Create: `app/mock_llm.py`
- Modify: `main.py` (the `get_llm` function, plus `import os`)
- Test: `tests/test_mock_llm.py`

**Interfaces:**
- Consumes: `SUPERVISOR_PROMPT` and `WORKER_PROMPTS` from `app.agents`; `run_task` from `app.orchestrator`; the history entry format `"[Iteration {i}] DELEGATE {worker}: ..."` and the `MUST` wording that `build_supervisor_prompt(force=True)` produces (both from Plan 1).
- Produces:
  - `app.mock_llm.complete(system: str, prompt: str) -> str` (async). It has the same signature as `app.llm.complete` and runs researcher, then writer, then reviewer, then FINAL.
  - `app.mock_llm.DELAY: float` (seconds per call, read from the `MOCK_LLM_DELAY` env var, default `0.8`)
  - `main.get_llm()` returns `mock_llm.complete` when the env var `MOCK_LLM == "1"`, and `app.llm.complete` otherwise

- [ ] **Step 1: Write the failing tests** — `tests/test_mock_llm.py`

```python
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


def test_get_llm_switches_on_env(monkeypatch):
    monkeypatch.delenv("MOCK_LLM", raising=False)
    assert get_llm() is claude.complete
    monkeypatch.setenv("MOCK_LLM", "1")
    assert get_llm() is mock_llm.complete
```

- [ ] **Step 2: Run and verify the tests fail**

Run: `.venv/bin/pytest tests/test_mock_llm.py -v`
Expected: ERROR with `ImportError: cannot import name 'mock_llm' from 'app'`

- [ ] **Step 3: Implement** — `app/mock_llm.py`

```python
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
    # The supervisor prompt carries the history ("DELEGATE <worker>: ...") and says MUST when forced.
    if "MUST" in prompt or "DELEGATE reviewer" in prompt:
        return _FINAL
    if "DELEGATE writer" in prompt:
        return "DELEGATE: reviewer\nTASK: Review the draft for accuracy, clarity and gaps."
    if "DELEGATE researcher" in prompt:
        return "DELEGATE: writer\nTASK: Turn the research notes into a short, well-structured report."
    return "DELEGATE: researcher\nTASK: Gather the key facts, figures and perspectives for the user's task."
```

Modify `main.py`. Add `import os` at the top with the other stdlib imports, change the app import line to `from app import llm as claude, mock_llm`, and replace `get_llm` with:

```python
def get_llm() -> LLM:
    return mock_llm.complete if os.environ.get("MOCK_LLM") == "1" else claude.complete
```

- [ ] **Step 4: Run and verify the tests pass**

Run: `.venv/bin/pytest -v`
Expected: all pass (the live test is skipped without a key)

- [ ] **Step 5: Commit**

```bash
git add app/mock_llm.py main.py tests/test_mock_llm.py
git commit -m "feat: add MOCK_LLM offline mode for demos without an API key" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Chat UI with live activity feed and final output panel

**Files:**
- Create: `static/index.html`, `static/styles.css`, `static/app.js`
- Modify: `main.py` (static mount + `/` route)
- Test: `tests/test_api.py` (append two tests)

**Interfaces:**
- Consumes: `parseSSE` from `static/sse.js`; the `POST /run/stream` event contract from Plan 1, Task 3; `GET /health`.
- Produces: `GET /` serves `static/index.html`, and `/static/*` serves the assets.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`

```python
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
```

- [ ] **Step 2: Run and verify the tests fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k "index or static"`
Expected: FAIL with `assert 404 == 200`

- [ ] **Step 3: Serve the static files** — modify `main.py`

Add these imports:

```python
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

Merge `FileResponse` into the existing `from fastapi.responses import StreamingResponse` line. After `app = FastAPI(...)`, add:

```python
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
```

- [ ] **Step 4: Write the UI**

Load the `frontend-design:frontend-design` skill first.

`static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Multi-Agent Supervisor</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js" defer></script>
  <script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js" defer></script>
  <script type="module" src="/static/app.js"></script>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <span class="logo" aria-hidden="true">◆</span>
      <div>
        <h1>Multi-Agent Supervisor</h1>
        <p>Supervisor · Researcher · Writer · Reviewer</p>
      </div>
    </div>
    <span id="health" class="health" data-ok="unknown">checking…</span>
  </header>

  <main class="layout">
    <section class="panel chat" aria-labelledby="chat-title">
      <h2 id="chat-title">Conversation</h2>
      <div id="conversation" class="conversation" aria-live="polite">
        <p class="empty">Submit a research task to watch the supervisor coordinate its team.</p>
      </div>
      <form id="task-form" class="composer">
        <label for="task" class="sr-only">Research task</label>
        <textarea id="task" rows="3" maxlength="4000" required
          placeholder="e.g. Explain how CRISPR gene editing works for a high-school audience"></textarea>
        <div class="composer-row">
          <label class="iters">Max iterations
            <input id="iterations" type="number" min="1" max="10" value="5">
          </label>
          <span class="hint">⌘/Ctrl + Enter</span>
          <button id="submit" type="submit">Run</button>
        </div>
      </form>
    </section>

    <aside class="side">
      <section class="panel" aria-labelledby="agents-title">
        <h2 id="agents-title">Agent activity</h2>
        <ul class="agents">
          <li data-agent="supervisor" data-state="idle"><span class="dot"></span><span class="name">Supervisor</span><span class="state"></span></li>
          <li data-agent="researcher" data-state="idle"><span class="dot"></span><span class="name">Researcher</span><span class="state"></span></li>
          <li data-agent="writer" data-state="idle"><span class="dot"></span><span class="name">Writer</span><span class="state"></span></li>
          <li data-agent="reviewer" data-state="idle"><span class="dot"></span><span class="name">Reviewer</span><span class="state"></span></li>
        </ul>
        <ol id="feed" class="feed" aria-live="polite"></ol>
      </section>

      <div id="error" class="error" role="alert" hidden></div>

      <section id="final" class="panel final" aria-labelledby="final-title" hidden>
        <div class="final-head">
          <h2 id="final-title">Final output</h2>
          <button id="copy" type="button" class="ghost">Copy</button>
        </div>
        <p id="final-meta" class="meta"></p>
        <div id="final-body" class="markdown"></div>
      </section>
    </aside>
  </main>
</body>
</html>
```

`static/styles.css`:

```css
:root {
  --bg: #f6f5f1; --panel: #ffffff; --text: #1d1d1f; --muted: #6b6b73; --border: #e3e1da;
  --accent: #4f46e5; --accent-text: #ffffff; --error-bg: #fdecec; --error-text: #9b1c1c;
  --supervisor: #7c3aed; --researcher: #0d9488; --writer: #d97706; --reviewer: #e11d48; --user: #334155;
  --radius: 14px; --shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 16px rgba(0,0,0,.04);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111114; --panel: #1a1a1f; --text: #ececf1; --muted: #9a9aa5; --border: #2a2a31;
    --accent: #818cf8; --accent-text: #0b0b0f; --error-bg: #3a1618; --error-text: #fca5a5;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--text); line-height: 1.5; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }

.topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 14px 20px; border-bottom: 1px solid var(--border); background: var(--panel); }
.brand { display: flex; align-items: center; gap: 12px; }
.brand h1 { margin: 0; font-size: 1.05rem; }
.brand p { margin: 0; font-size: .8rem; color: var(--muted); }
.logo { font-size: 1.5rem; color: var(--accent); }
.health { font-size: .75rem; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
.health[data-ok="true"] { color: var(--researcher); border-color: currentColor; }
.health[data-ok="false"] { color: var(--error-text); border-color: currentColor; }

.layout { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 16px;
  max-width: 1280px; margin: 0 auto; padding: 16px; }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 16px; }
.panel h2 { margin: 0 0 12px; font-size: .8rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
.side { display: flex; flex-direction: column; gap: 16px; min-width: 0; }

.chat { display: flex; flex-direction: column; height: calc(100vh - 100px); min-height: 480px; }
.conversation { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding-right: 4px; }
.empty { color: var(--muted); margin: auto; text-align: center; max-width: 32ch; }

.msg { border: 1px solid var(--border); border-left: 4px solid var(--agent-color, var(--border));
  border-radius: 10px; padding: 10px 12px; background: var(--bg); }
.msg header { display: flex; justify-content: space-between; gap: 8px; font-size: .8rem; font-weight: 600; color: var(--agent-color); }
.msg header .iter { color: var(--muted); font-weight: 400; }
.msg summary { cursor: pointer; list-style: none; }
.msg summary::-webkit-details-marker { display: none; }
.msg summary::after { content: "show"; float: right; font-size: .75rem; color: var(--muted); }
.msg details[open] summary::after { content: "hide"; }
.msg-user { --agent-color: var(--user); align-self: flex-end; max-width: 85%; background: var(--panel); }
.msg-supervisor { --agent-color: var(--supervisor); }
.msg-researcher { --agent-color: var(--researcher); }
.msg-writer { --agent-color: var(--writer); }
.msg-reviewer { --agent-color: var(--reviewer); }
.msg-system { --agent-color: var(--error-text); }

.composer { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 12px; }
.composer textarea { width: 100%; resize: vertical; font: inherit; color: inherit; background: var(--bg);
  border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
.composer textarea:focus, .composer input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.composer-row { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.iters { font-size: .85rem; color: var(--muted); display: flex; align-items: center; gap: 6px; }
.iters input { width: 4.5em; font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 4px 6px; }
.hint { margin-left: auto; font-size: .75rem; color: var(--muted); }
button { font: inherit; font-weight: 600; border: 0; border-radius: 10px; padding: 8px 18px; cursor: pointer;
  background: var(--accent); color: var(--accent-text); }
button:disabled { opacity: .55; cursor: progress; }
button.ghost { background: transparent; color: var(--muted); border: 1px solid var(--border); padding: 4px 10px; font-size: .8rem; }

.agents { list-style: none; margin: 0 0 12px; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.agents li { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 10px; font-size: .85rem; }
.agents li[data-agent="supervisor"] { --agent-color: var(--supervisor); }
.agents li[data-agent="researcher"] { --agent-color: var(--researcher); }
.agents li[data-agent="writer"] { --agent-color: var(--writer); }
.agents li[data-agent="reviewer"] { --agent-color: var(--reviewer); }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--border); flex: none; }
.state { margin-left: auto; font-size: .7rem; color: var(--muted); }
.agents li[data-state="working"] { border-color: var(--agent-color); }
.agents li[data-state="working"] .dot { background: var(--agent-color); animation: pulse 1s ease-in-out infinite; }
.agents li[data-state="working"] .state::after { content: "working"; color: var(--agent-color); }
.agents li[data-state="done"] .dot { background: var(--agent-color); opacity: .45; }
.agents li[data-state="done"] .state::after { content: "done"; }
@keyframes pulse { 50% { transform: scale(1.5); opacity: .5; } }
@media (prefers-reduced-motion: reduce) { .agents li .dot { animation: none !important; } }

.feed { list-style: none; margin: 0; padding: 0; max-height: 220px; overflow-y: auto; font-size: .8rem; }
.feed li { display: flex; gap: 8px; padding: 4px 0; border-top: 1px dashed var(--border); }
.feed time { color: var(--muted); font-variant-numeric: tabular-nums; flex: none; }
.feed .who { font-weight: 600; color: var(--agent-color, var(--muted)); flex: none; text-transform: capitalize; }

.final-head { display: flex; align-items: center; justify-content: space-between; }
.final-head h2 { margin: 0; }
.meta { margin: 4px 0 12px; font-size: .8rem; color: var(--muted); }
.markdown { overflow-wrap: anywhere; }
.markdown :first-child { margin-top: 0; }
.markdown pre { overflow-x: auto; background: var(--bg); padding: 10px; border-radius: 8px; }
.markdown code { font-size: .9em; }
.markdown table { border-collapse: collapse; display: block; overflow-x: auto; }
.markdown th, .markdown td { border: 1px solid var(--border); padding: 4px 8px; }

.error { background: var(--error-bg); color: var(--error-text); border-radius: var(--radius); padding: 12px 16px; font-size: .9rem; }

@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; padding: 12px; }
  .chat { height: auto; min-height: 0; }
  .conversation { max-height: 60vh; }
  .hint { display: none; }
  .composer-row button { margin-left: auto; }
}
```

`static/app.js`:

```js
import { parseSSE } from "./sse.js";

const $ = (selector) => document.querySelector(selector);
const form = $("#task-form");
const taskInput = $("#task");
const itersInput = $("#iterations");
const submitBtn = $("#submit");
const conversation = $("#conversation");
const feed = $("#feed");
const finalPanel = $("#final");
const finalBody = $("#final-body");
const finalMeta = $("#final-meta");
const errorBox = $("#error");
const AGENTS = ["supervisor", "researcher", "writer", "reviewer"];
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

let running = false;
let lastOutput = "";

// Model output is untrusted: always sanitize. Fall back to escaped text if the CDN scripts are missing.
function md(text) {
  if (!window.marked || !window.DOMPurify) {
    const pre = document.createElement("pre");
    pre.textContent = text;
    return pre.outerHTML;
  }
  return DOMPurify.sanitize(marked.parse(text));
}

function agentEl(agent) {
  return document.querySelector(`[data-agent="${agent}"]`);
}

function setAgentState(agent, state) {
  const el = agentEl(agent);
  if (el) el.dataset.state = state;
}

function finishWorkingAgents() {
  AGENTS.forEach((a) => { if (agentEl(a).dataset.state === "working") setAgentState(a, "done"); });
}

function logFeed(agent, text) {
  const li = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = agent;
  if (AGENTS.includes(agent)) who.style.setProperty("--agent-color", `var(--${agent})`);
  const what = document.createElement("span");
  what.textContent = text;
  li.append(time, who, what);
  feed.append(li);
  feed.scrollTop = feed.scrollHeight;
}

function addMessage(role, title, body, { iteration, collapsible = false } = {}) {
  conversation.querySelector(".empty")?.remove();
  const article = document.createElement("article");
  article.className = `msg msg-${role}`;
  const header = document.createElement("header");
  const name = document.createElement("span");
  name.textContent = title;
  header.append(name);
  if (iteration) {
    const iter = document.createElement("span");
    iter.className = "iter";
    iter.textContent = `iteration ${iteration}`;
    header.append(iter);
  }
  const content = document.createElement("div");
  content.className = "markdown";
  content.innerHTML = md(body);
  if (collapsible) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.append(header);
    details.append(summary, content);
    article.append(details);
  } else {
    article.append(header, content);
  }
  conversation.append(article);
  conversation.scrollTop = conversation.scrollHeight;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function handle(event) {
  switch (event.type) {
    case "start":
      logFeed("system", `Task received · up to ${event.max_iterations} iterations`);
      break;
    case "agent_start":
      finishWorkingAgents();
      setAgentState(event.agent, "working");
      logFeed(event.agent, event.forced ? "Iteration limit reached, forcing a final answer" : `Working (iteration ${event.iteration})`);
      break;
    case "supervisor":
      setAgentState("supervisor", "done");
      if (event.action === "delegate") {
        addMessage("supervisor", `Supervisor → ${cap(event.worker)}`, event.task, { iteration: event.iteration });
        logFeed("supervisor", `Delegated to ${event.worker}`);
      } else if (event.action === "invalid") {
        addMessage("supervisor", "Supervisor · invalid delegation", `Tried to delegate to \`${event.worker}\`, which is not a worker. Retrying.`, { iteration: event.iteration });
        logFeed("supervisor", `Invalid worker "${event.worker}"`);
      } else {
        addMessage("supervisor", "Supervisor · final decision",
          event.forced ? "Iteration limit reached. Synthesizing the best answer from the work so far." : "Synthesizing the team's work into the final answer.",
          { iteration: event.iteration });
        logFeed("supervisor", "Synthesizing final answer");
      }
      break;
    case "worker_result":
      setAgentState(event.agent, "done");
      addMessage(event.agent, `${cap(event.agent)} result`, event.result, { iteration: event.iteration, collapsible: true });
      logFeed(event.agent, "Finished");
      break;
    case "final":
      finishWorkingAgents();
      lastOutput = event.output;
      finalBody.innerHTML = md(event.output);
      finalMeta.textContent = `${event.iterations} iteration${event.iterations === 1 ? "" : "s"}${event.forced ? " · stopped by iteration limit" : ""}`;
      finalPanel.hidden = false;
      logFeed("system", "Done");
      break;
    case "error":
      finishWorkingAgents();
      showError(event.message);
      addMessage("system", "Error", event.message);
      logFeed("system", `Error: ${event.message}`);
      break;
  }
}

function formatDetail(detail) {
  if (Array.isArray(detail)) return detail.map((d) => `${d.loc?.at(-1) ?? "input"}: ${d.msg}`).join("; ");
  return typeof detail === "string" ? detail : "";
}

async function runTask(task, maxIterations) {
  const res = await fetch("/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task, max_iterations: maxIterations }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(formatDetail(body.detail) || `Request failed (HTTP ${res.status})`);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    let events;
    [events, buffer] = parseSSE(buffer + value);
    for (const event of events) {
      if (event.type === "final" || event.type === "error") finished = true;
      handle(event);
    }
  }
  if (!finished) throw new Error("The connection closed before the run finished. Please try again.");
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const task = taskInput.value.trim();
  if (!task || running) return;
  running = true;
  submitBtn.disabled = true;
  submitBtn.textContent = "Running…";
  feed.replaceChildren();
  finalPanel.hidden = true;
  errorBox.hidden = true;
  AGENTS.forEach((a) => setAgentState(a, "idle"));
  addMessage("user", "You", task);
  try {
    await runTask(task, Number(itersInput.value));
  } catch (err) {
    handle({ type: "error", message: err.message });
  } finally {
    running = false;
    submitBtn.disabled = false;
    submitBtn.textContent = "Run";
  }
});

taskInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) form.requestSubmit();
});

$("#copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(lastOutput);
    $("#copy").textContent = "Copied";
    setTimeout(() => ($("#copy").textContent = "Copy"), 1500);
  } catch {
    showError("Could not copy to clipboard.");
  }
});

fetch("/health")
  .then((r) => r.json())
  .then((d) => { const h = $("#health"); h.dataset.ok = String(d.status === "ok"); h.textContent = d.status === "ok" ? "online" : "degraded"; })
  .catch(() => { const h = $("#health"); h.dataset.ok = "false"; h.textContent = "offline"; });
```

- [ ] **Step 5: Run all automated tests**

Run: `.venv/bin/pytest -v && node --test tests/js/*.test.mjs`
Expected: all pass (the live test is skipped without a key)

- [ ] **Step 6: Verify in a real browser (MOCK_LLM mode)**

```bash
MOCK_LLM=1 .venv/bin/uvicorn main:app --port 8000
```

Run the server in the background. Load the `claude-in-chrome` skill (or `anthropic-skills:chrome-browser`), open `http://localhost:8000` in a new tab, and check each of the following:

1. The health pill says "online".
2. Submit "Explain the supervisor pattern" with 5 iterations. During the run, the agent chips light up in order (supervisor, researcher, supervisor, writer, supervisor, reviewer, supervisor), and the feed logs each step with timestamps.
3. The conversation shows the user bubble, three "Supervisor → X" cards, and three collapsible worker results that expand on click.
4. The final panel renders Markdown (heading, list, blockquote), and the meta line reads "4 iterations".
5. While a run is in progress, the Run button is disabled and clicking it again does nothing.
6. Set iterations to 1 and run again. The meta line reads "1 iteration · stopped by iteration limit".
7. Use DevTools or `javascript_tool` to set the iterations input's `max` to 50 and its value to 50, then submit. The error box shows `max_iterations: Input should be less than or equal to 10`.
8. XSS check: using `javascript_tool`, evaluate `DOMPurify.sanitize(marked.parse('<img src=x onerror=console.log(1)>'))` and confirm the result has no `onerror`. Also `grep -n innerHTML static/app.js` and confirm every assignment goes through `md()`. **Do not** inject unsanitized HTML with `alert`: an alert dialog blocks the browser tools.
9. Resize to 390×844 (mobile). The layout stacks in a single column, there is no horizontal scroll, and the composer stays usable.
10. Stop the server mid-run: start a run with `MOCK_LLM_DELAY=3` and kill uvicorn. The UI shows the "connection closed" error and the Run button is re-enabled.

Save a screenshot of the finished desktop run to `docs/screenshot.png` (the README uses it in Plan 3). If any check fails, fix it, re-run Step 5, and repeat the check.

- [ ] **Step 7: Commit**

```bash
git add static/index.html static/styles.css static/app.js main.py tests/test_api.py docs/screenshot.png
git commit -m "feat: add chat UI with live agent activity feed and final output panel" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
