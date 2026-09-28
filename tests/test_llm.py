import asyncio
from types import SimpleNamespace

import pytest

from app import llm


class StubMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def install_stub(monkeypatch, content, stop_reason="end_turn"):
    messages = StubMessages(SimpleNamespace(content=content, stop_reason=stop_reason))
    monkeypatch.setattr(llm, "_client", SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def test_complete_joins_text_blocks_and_skips_thinking(monkeypatch):
    stub = install_stub(monkeypatch, [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="Hello "),
        SimpleNamespace(type="text", text="world"),
    ])

    assert asyncio.run(llm.complete("sys", "hi")) == "Hello world"
    assert stub.kwargs["model"] == llm.MODEL
    assert stub.kwargs["system"] == "sys"
    assert stub.kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert stub.kwargs["thinking"] == {"type": "adaptive"}
    assert stub.kwargs["output_config"] == {"effort": llm.EFFORT}
    assert stub.kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert stub.kwargs["extra_body"] == {"fallbacks": "default"}


def test_complete_raises_on_refusal(monkeypatch):
    install_stub(monkeypatch, [], stop_reason="refusal")
    with pytest.raises(llm.LLMError, match="declined"):
        asyncio.run(llm.complete("sys", "hi"))


def test_complete_raises_on_empty_text(monkeypatch):
    install_stub(monkeypatch, [SimpleNamespace(type="thinking", thinking="")])
    with pytest.raises(llm.LLMError, match="empty"):
        asyncio.run(llm.complete("sys", "hi"))
