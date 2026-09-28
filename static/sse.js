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
