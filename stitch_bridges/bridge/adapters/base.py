"""Adapter contract: one upstream service behind the bridge surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol


class AccountError(Exception):
    """Account-level failure (auth/limit): park the account for a cooldown."""


class TransientError(Exception):
    """Model/session/timeout failure: retry elsewhere, keep the account."""


class BridgeAdapter(Protocol):
    """A reverse-engineered web service exposed as an OpenAI-ish provider."""

    name: str

    async def list_models(self) -> list[str]:
        """OpenAI-visible model ids this adapter serves."""

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> AsyncIterator[str]:
        """Yield assistant text deltas for *model* (flattened conversation)."""


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Collapse an OpenAI message list into a single prompt (last user first)."""
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        text = str(content or "").strip()
        if not text:
            continue
        role = str(message.get("role", "user"))
        parts.append(f"{role}: {text}" if len(messages) > 1 else text)
    return "\n\n".join(parts)
