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
  try {
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
  } catch (err) {
    if (finished) throw err;
    // The server dropped the connection mid-stream (reader.read() rejects with a raw
    // browser error like "network error"); fall through to the friendlier message below.
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
