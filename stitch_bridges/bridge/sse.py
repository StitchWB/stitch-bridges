"""SSE helpers for the OpenAI-compatible bridge surface."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from typing import Any


def sse_data(payload: Any) -> bytes:
    """One ``data:`` frame (JSON-encoded) terminated by a blank line."""
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {body}\n\n".encode()


def sse_done() -> bytes:
    return b"data: [DONE]\n\n"


async def sse_stream(chunks: AsyncIterator[dict[str, Any]], *, with_done: bool = True) -> AsyncIterator[bytes]:
    """Proxy OpenAI-style chunk dicts as SSE frames, byte-for-byte."""
    async for chunk in chunks:
        yield sse_data(chunk)
    if with_done:
        yield sse_done()


async def iter_text_deltas(deltas: Iterable[str]) -> AsyncIterator[dict[str, Any]]:
    """Adapt plain string deltas into minimal chunk payloads (tests/adapters)."""
    for text in deltas:
        if text:
            yield {"choices": [{"delta": {"content": text}}]}
