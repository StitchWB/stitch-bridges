"""Loopback OpenAI-compatible surface served by the bridge plugin."""

from __future__ import annotations

import hmac
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .adapters.base import BridgeAdapter, TransientError
from .sse import sse_data, sse_done


def _bearer_ok(request: Request, api_key: str) -> bool:
    if not api_key:
        return True
    raw = request.headers.get("authorization", "")
    candidate = raw[7:] if raw.lower().startswith("bearer ") else raw
    return hmac.compare_digest(candidate, api_key)


def _chunk(model: str, *, delta: str = "", finish: str | None = None) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": delta} if delta else {},
                "finish_reason": finish,
            }
        ],
    }


def build_app(adapter: BridgeAdapter, *, api_key: str = "") -> FastAPI:
    app = FastAPI(title=f"stitch-bridge:{adapter.name}", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "adapter": adapter.name}

    @app.get("/v1/models")
    async def models(request: Request) -> Any:
        if not _bearer_ok(request, api_key):
            return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
        ids = await adapter.list_models()
        return {
            "object": "list",
            "data": [
                {"id": model_id, "object": "model", "owned_by": adapter.name}
                for model_id in ids
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        if not _bearer_ok(request, api_key):
            return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
        body = await request.json()
        model = str(body.get("model", "")).strip()
        messages = body.get("messages") or []
        if not model or not isinstance(messages, list):
            return JSONResponse({"error": {"message": "model and messages required"}}, status_code=400)
        options = {
            "reasoning_effort": body.get("reasoning_effort"),
            "web_search": body.get("web_search"),
        }

        if body.get("stream"):
            async def frames() -> AsyncIterator[bytes]:
                yield sse_data(_chunk(model, delta=""))
                try:
                    async for delta in adapter.chat_stream(
                        model=model, messages=messages, options=options
                    ):
                        yield sse_data(_chunk(model, delta=delta))
                except TransientError as exc:
                    yield sse_data({"error": {"message": str(exc)}})
                yield sse_data(_chunk(model, finish="stop"))
                yield sse_done()

            return StreamingResponse(frames(), media_type="text/event-stream")

        text = ""
        try:
            async for delta in adapter.chat_stream(model=model, messages=messages, options=options):
                text += delta
        except TransientError as exc:
            return JSONResponse({"error": {"message": str(exc)}}, status_code=502)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }

    return app
