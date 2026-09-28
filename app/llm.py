"""The only module that talks to Claude. Every agent calls complete()."""
import os

import anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
EFFORT = os.environ.get("ANTHROPIC_EFFORT", "medium")

_client: anthropic.AsyncAnthropic | None = None


class LLMError(Exception):
    """Claude answered, but not with usable text."""


def _get_client() -> anthropic.AsyncAnthropic:
    # Lazy so importing the app never needs credentials (tests, MOCK_LLM mode).
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def complete(system: str, prompt: str) -> str:
    response = await _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},  # re-run refused requests on Anthropic's recommended model
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise LLMError("Claude returned an empty response.")
    return text
