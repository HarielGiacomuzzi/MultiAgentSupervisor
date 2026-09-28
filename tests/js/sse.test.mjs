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
