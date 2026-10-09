"""Unit tests for the bridge core, the Notion adapter and the HTTP surface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from stitch_bridges.bridge.adapters.base import TransientError, flatten_messages
from stitch_bridges.bridge.adapters.notion import NotionAdapter
from stitch_bridges.bridge.app import build_app
from stitch_bridges.bridge.pool import Account, AccountPool


def test_flatten_messages_joins_roles() -> None:
    prompt = flatten_messages(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert prompt == "system: be terse\n\nuser: hi"
    assert flatten_messages([{"role": "user", "content": "hi"}]) == "hi"


def test_pool_cooldown_and_concurrency() -> None:
    account = Account(id="a", credential="token")
    pool = AccountPool([account], cooldown_s=60.0, max_concurrency=1)
    picked = pool.pick()
    assert picked is account
    assert pool.pick() is None  # busy (max_concurrency=1)
    pool.release(picked, error="auth 401")
    assert not account.available()
    assert pool.pick() is None  # parked by the cooldown
    stats = pool.stats()
    assert stats[0]["errors"] == 1 and stats[0]["last_error"] == "auth 401"


def test_pool_weight_selection_prefers_heavier() -> None:
    light = Account(id="light", credential="t", weight=1)
    heavy = Account(id="heavy", credential="t", weight=1000)
    pool = AccountPool([light, heavy], max_concurrency=1)
    picks: list[Account] = []
    for _ in range(20):
        account = pool.pick()
        assert account is not None
        picks.append(account)
        pool.release(account)
    assert sum(1 for a in picks if a is heavy) > sum(1 for a in picks if a is light)


def test_notion_adapter_model_map_and_slug() -> None:
    adapter = NotionAdapter(AccountPool([]))
    models = asyncio.run(adapter.list_models())
    assert models, "bundled registry must expose models"
    assert adapter.resolve_slug(models[0]) in models or adapter.resolve_slug(models[0])
    with pytest.raises(TransientError):
        adapter.resolve_slug("")


def test_notion_adapter_parses_transcript() -> None:
    class _Response:
        def json(self) -> dict[str, Any]:
            return {
                "patches": [
                    {"op": "session", "session": {"status": "finished"}},
                    {
                        "op": "put",
                        "entity": {
                            "kind": "assistant_message",
                            "content": [{"type": "text", "text": "hello \\*world\\*"}],
                        },
                    },
                ]
            }

    text, done = NotionAdapter._parse(_Response())
    assert text == "hello *world*"
    assert done is True


class _FakeAdapter:
    name = "fake"

    async def list_models(self) -> list[str]:
        return ["fake-1"]

    async def chat_stream(self, *, model: str, messages: list, options: dict):
        for part in ("hel", "lo"):
            yield part


def _client(adapter: Any, api_key: str = "k") -> AsyncClient:
    app = build_app(adapter, api_key=api_key)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://bridge")


def test_app_requires_api_key() -> None:
    async def run() -> None:
        async with _client(_FakeAdapter()) as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/v1/models")).status_code == 401
            ok = await client.get("/v1/models", headers={"Authorization": "Bearer k"})
            assert ok.status_code == 200 and ok.json()["data"][0]["id"] == "fake-1"

    asyncio.run(run())


def test_app_streams_and_completes() -> None:
    async def run() -> None:
        async with _client(_FakeAdapter()) as client:
            headers = {"Authorization": "Bearer k"}
            stream = await client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": "fake-1", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            )
            assert stream.status_code == 200
            assert "hel" in stream.text and "[DONE]" in stream.text
            full = await client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": "fake-1", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert full.json()["choices"][0]["message"]["content"] == "hello"

    asyncio.run(run())


def test_manifest_labels_resolve() -> None:
    """Every manifest label must resolve through the plugin-id-keyed bundle."""
    manifest_path = Path(__file__).resolve().parents[1] / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pid = manifest["id"]
    i18n = manifest["contributions"]["i18n"]

    def walk(bundle: Any, parts: list[str]) -> Any:
        value = bundle
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return None
        return value if isinstance(value, str) else None

    keys: set[str] = set()
    ui = manifest["contributions"]["ui"]
    for tab in ui["tabs"]:
        keys.add(tab["label"])
    page = ui["page"]
    keys.add(page["title"])

    def visit(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            for field in ("text", "title", "label", "placeholder"):
                value = node.get(field)
                if isinstance(value, str) and value.startswith(f"{pid}."):
                    keys.add(value)
            for column in node.get("columns", []):
                if str(column.get("label", "")).startswith(f"{pid}."):
                    keys.add(column["label"])
            for action in node.get("rowActions", []):
                if str(action.get("label", "")).startswith(f"{pid}."):
                    keys.add(action["label"])
            visit(node.get("nodes", []))

    visit(page["nodes"])
    for key in keys:
        parts = key.split(".")
        assert walk(i18n["ru"], parts), key
        assert walk(i18n["en"], parts), key
