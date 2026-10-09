"""Notion agent adapter: reverse-engineered agent chat as a provider.

Protocol (browser agent chat, 2026-10):

    POST /api/v3/createAgentThread      {type: personal_agent, model: <slug>, …}
    POST /api/v3/getThreadTranscript    poll {spaceId, threadId} -> patches
      patches: {op: "session", session.status} | {op: "put", entity: assistant_message}

Requests must impersonate Chrome's TLS fingerprint (plain clients get
``400 UserValidationError``); the account pool supplies sticky sessions.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..pool import Account, AccountPool
from .base import AccountError, TransientError, flatten_messages

NOTION_URL = "https://www.notion.so"

_POLL_INTERVAL_S = 1.0
_POLL_TIMEOUT_S = 180.0
_STABLE_POLLS = 3
_DONE_STATUSES = ("finished", "complete", "done", "idle")
_FAILED_STATUSES = ("failed", "error")

_MD_UNESCAPE = re.compile(r"(?<!\\)\\([_*`~\[\]()#>+\-.!|{}])")

_MODELS_PATH = Path(__file__).resolve().parents[2] / "data" / "notion_models.json"


def _alias(name: str, slug: str) -> str:
    alias = re.sub(r"[^a-z0-9.]+", "-", name.strip().lower()).strip("-")
    return alias or slug


def load_model_map() -> dict[str, str]:
    """alias -> internal Notion model slug (bundled registry snapshot).

    The registry maps ``slug -> {name, family, efforts, disabled}``; we expose
    a stable human alias (from ``name``) plus the raw slug for each enabled one.
    """
    try:
        raw = json.loads(_MODELS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries: dict[str, Any] = raw.get("models") if isinstance(raw.get("models"), dict) else raw
    out: dict[str, str] = {}
    for slug, meta in entries.items():
        if isinstance(meta, dict):
            if meta.get("disabled"):
                continue
            alias = _alias(str(meta.get("name") or slug), str(slug))
            out[alias] = str(slug)
            out.setdefault(str(slug), str(slug))
        elif isinstance(meta, str):
            out[str(slug)] = meta
    return out


class NotionAdapter:
    """Notion agent chat behind the bridge contract."""

    name = "notion"

    def __init__(self, pool: AccountPool, *, model_map: dict[str, str] | None = None) -> None:
        self._pool = pool
        self._models = model_map or load_model_map()

    async def list_models(self) -> list[str]:
        return sorted(self._models)

    def resolve_slug(self, model: str) -> str:
        slug = self._models.get(model, model)
        if not slug:
            raise TransientError(f"unknown model: {model}")
        return slug

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> AsyncIterator[str]:
        slug = self.resolve_slug(model)
        prompt = flatten_messages(messages)
        effort = str(options.get("reasoning_effort") or "").strip()
        web_search = bool(options.get("web_search"))
        last_error = ""
        for _attempt in range(max(1, len(self._pool))):
            account = self._pool.pick()
            if account is None:
                break
            try:
                async for delta in self._run(account, prompt, slug, effort, web_search):
                    yield delta
                self._pool.release(account)
                return
            except AccountError as exc:
                last_error = str(exc)
                self._pool.release(account, error=last_error)
            except TransientError as exc:
                last_error = str(exc)
                self._pool.release(account)
        raise TransientError(last_error or "no available account")

    async def _run(
        self,
        account: Account,
        prompt: str,
        slug: str,
        effort: str,
        web_search: bool,
    ) -> AsyncIterator[str]:
        session = account.session_or_create()
        meta = account.meta if isinstance(getattr(account, "meta", None), dict) else {}
        space_id = str(meta.get("space_id") or "")
        cookies = {"token_v2": account.credential}
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Referer": f"{NOTION_URL}/chat",
        }
        thread_id = str(uuid.uuid4())
        payload = {
            "type": "personal_agent",
            "spaceId": space_id,
            "threadId": thread_id,
            "enableSuggestedEditsTools": True,
            "model": slug,
            "createdSource": "full_page_chat",
            "content": [{"type": "text", "text": [[prompt]]}],
            "policies": {"approval_mode": "review_for_me"},
            "agentMemorySettings": {"useMemories": False, "excludeChatFromMemories": True},
            "browserEnabled": web_search,
            "clientMessageId": str(uuid.uuid4()),
        }
        if effort:
            payload["reasoningEffort"] = effort

        response = await session.post(
            f"{NOTION_URL}/api/v3/createAgentThread",
            json=payload,
            headers=headers,
            cookies=cookies,
        )
        if response.status_code >= 400:
            raise self._classify(response.status_code, response.text)

        deadline = time.monotonic() + _POLL_TIMEOUT_S
        emitted = ""
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)
            try:
                poll = await session.post(
                    f"{NOTION_URL}/api/v3/getThreadTranscript",
                    json={
                        "spaceId": space_id,
                        "threadId": thread_id,
                        "direction": "backward",
                        "limit": 50,
                    },
                    headers=headers,
                    cookies=cookies,
                )
            except Exception:  # noqa: BLE001 - transient transport errors retry
                continue
            if poll.status_code in (401, 403):
                raise AccountError(f"transcript auth {poll.status_code}")
            if poll.status_code != 200:
                continue
            text, done = self._parse(poll)
            if text and text != emitted:
                delta = text[len(emitted):] if text.startswith(emitted) else text
                emitted = text
                stable = 0
                if delta:
                    yield delta
            elif text:
                stable += 1
            if done or stable >= _STABLE_POLLS:
                break
        if not emitted:
            raise TransientError("no assistant message in transcript (timeout)")

    @staticmethod
    def _classify(status: int, body: str) -> Exception:
        if status in (401, 403, 429):
            return AccountError(f"auth/limit {status}")
        return TransientError(f"http {status}: {body[:120]}")

    @staticmethod
    def _parse(response: Any) -> tuple[str, bool]:
        """Return (assistant_text, session_finished) from a transcript poll."""
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return "", False
        parts: list[str] = []
        done = False
        for patch in data.get("patches", []) or []:
            op = patch.get("op")
            if op == "session":
                status = str((patch.get("session") or {}).get("status", ""))
                if status in _FAILED_STATUSES:
                    raise TransientError(f"agent session {status}")
                if status in _DONE_STATUSES:
                    done = True
            elif op == "put":
                entity = patch.get("entity") or {}
                if entity.get("kind") == "assistant_message":
                    for block in entity.get("content", []) or []:
                        if block.get("type") == "text" and block.get("text"):
                            parts.append(_MD_UNESCAPE.sub(r"\1", str(block["text"])))
        return "\n".join(parts), done
