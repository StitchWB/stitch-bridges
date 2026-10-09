"""Command layer: owns the bridge process, its accounts and its state file.

The bridge HTTP surface runs on loopback inside this plugin process and its
endpoint is published to a HMAC-signed ``bridge_state.json`` so the hub core
can register a sidecar-backed inference provider (same contract as the
``stitch-freemodel`` bridge).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any

from . import crypto, storage
from .bridge.adapters.notion import NotionAdapter
from .bridge.pool import Account, AccountPool

PLUGIN_ID = "stitch-bridges"
ADAPTER_NAME = "notion"
STATE_FILENAME = "bridge_state.json"
STATE_KEY_FILENAME = "state.key"

_db_path: str = ""
_data_dir: str = ""
_server: Any = None
_thread: threading.Thread | None = None
_endpoint: str = ""
_pool: AccountPool | None = None


def configure(*, db_path: str, data_dir: str) -> None:
    global _db_path, _data_dir
    _db_path = db_path
    _data_dir = data_dir


def _data_path() -> Path:
    return Path(_data_dir or ".")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _load_pool() -> AccountPool:
    pool = AccountPool(cooldown_s=120.0)
    data_dir = _data_path()
    for row in storage.list_accounts(_db_path):
        try:
            credential = crypto.decrypt(data_dir, str(row["credential"]))
        except Exception:  # noqa: BLE001 - skip unreadable rows, keep the rest
            continue
        try:
            meta = json.loads(str(row["meta"]) or "{}")
        except ValueError:
            meta = {}
        pool.add(
            Account(
                id=str(row["id"]),
                credential=credential,
                weight=int(row["weight"]),
                proxy=str(row["proxy"]) or None,
                meta=meta if isinstance(meta, dict) else {},
            )
        )
    return pool


def _write_state(running: bool) -> None:
    """Publish the endpoint + pool stats under a HMAC-signed state file."""
    data_dir = _data_path()
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "plugin_id": PLUGIN_ID,
        "sidecar_name": ADAPTER_NAME,
        "running": running,
        "endpoint": _endpoint,
        "accounts": len(_pool) if _pool else 0,
        "updated_at": time.time(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key_path = data_dir / STATE_KEY_FILENAME
    if not key_path.is_file():
        key_path.write_bytes(secrets.token_bytes(32))
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
    key = key_path.read_bytes()
    payload["hmac"] = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    (data_dir / STATE_FILENAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _start_bridge() -> dict[str, Any]:
    global _server, _thread, _endpoint, _pool
    if _thread is not None and _thread.is_alive():
        return {"running": True, "endpoint": _endpoint}
    import uvicorn  # noqa: PLC0415

    from .bridge.app import build_app  # noqa: PLC0415

    _pool = _load_pool()
    api_key = storage.get_setting(_db_path, "api_key")
    app = build_app(NotionAdapter(_pool), api_key=api_key)
    port = _free_port()
    _endpoint = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    _server = uvicorn.Server(config)
    _thread = threading.Thread(target=_server.run, name="stitch-bridge", daemon=True)
    _thread.start()
    _write_state(True)
    return {"running": True, "endpoint": _endpoint, "accounts": len(_pool)}


def _stop_bridge() -> dict[str, Any]:
    global _server, _thread, _endpoint
    if _server is not None:
        _server.should_exit = True
    if _thread is not None:
        _thread.join(timeout=5)
    _server = None
    _thread = None
    _endpoint = ""
    _write_state(False)
    return {"running": False}


def health_check() -> dict[str, Any]:
    running = bool(_thread is not None and _thread.is_alive())
    return {
        "ok": True,
        "running": running,
        "endpoint": _endpoint,
        "accounts": len(_pool) if _pool else 0,
    }


def status() -> dict[str, Any]:
    return {
        "running": bool(_thread is not None and _thread.is_alive()),
        "endpoint": _endpoint,
        "accounts": _pool.stats() if _pool else [],
    }


def bridges_list() -> list[dict[str, Any]]:
    running = bool(_thread is not None and _thread.is_alive())
    return [
        {
            "name": ADAPTER_NAME,
            "running": "● да" if running else "—",
            "endpoint": _endpoint or "—",
            "accounts": len(_pool) if _pool else 0,
        }
    ]


def models() -> list[str]:
    return sorted(NotionAdapter(_pool or AccountPool())._models)


def accounts_list() -> list[dict[str, Any]]:
    data_dir = _data_path()
    rows = []
    for row in storage.list_accounts(_db_path):
        try:
            credential = crypto.decrypt(data_dir, str(row["credential"]))
        except Exception:  # noqa: BLE001
            credential = ""
        rows.append(
            {
                "id": row["id"],
                "weight": row["weight"],
                "proxy": row["proxy"] or "—",
                "has_credential": bool(credential),
            }
        )
    return rows


def accounts_add(params: dict[str, Any]) -> dict[str, Any]:
    credential = str(params.get("credential", "")).strip()
    if not credential:
        raise ValueError("credential is required")
    account_id = str(params.get("id") or "").strip() or secrets.token_hex(4)
    meta = {
        "space_id": str(params.get("space_id", "")).strip(),
    }
    storage.add_account(
        _db_path,
        account_id=account_id,
        credential=crypto.encrypt(_data_path(), credential),
        weight=int(params.get("weight", 1) or 1),
        proxy=str(params.get("proxy", "")).strip(),
        meta=json.dumps(meta),
    )
    if _pool is not None:
        _pool.add(Account(id=account_id, credential=credential, meta=meta))
        _write_state(True)
    return {"id": account_id, "added": True}


def accounts_remove(params: dict[str, Any]) -> dict[str, Any]:
    account_id = str(params.get("id", "")).strip()
    if not account_id:
        raise ValueError("id is required")
    removed = storage.remove_account(_db_path, account_id)
    if _pool is not None:
        _pool.remove(account_id)
        _write_state(True)
    return {"id": account_id, "removed": removed}


def bridge_start(params: dict[str, Any]) -> dict[str, Any]:
    return _start_bridge()


def bridge_stop(params: dict[str, Any]) -> dict[str, Any]:
    return _stop_bridge()


def set_api_key(params: dict[str, Any]) -> dict[str, Any]:
    value = str(params.get("api_key", "")).strip()
    storage.set_setting(_db_path, "api_key", value)
    return {"api_key_set": bool(value)}
