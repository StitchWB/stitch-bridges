"""RPC entry point for the stitch-bridges service plugin.

Spawned by ``ServicePluginHost`` as ``python -m stitch_bridges``.  Owns the
loopback OpenAI-compatible bridge and publishes its endpoint for the hub's
sidecar-backed inference provider.
"""

# _generated_by: stitch_plugin_tools scaffold v3

from __future__ import annotations

from typing import Any

from . import service, storage

try:
    from autoreg.plugin.rpc import RpcPluginServer
except ImportError:
    from ._vendor.rpc_server import RpcPluginServer


class _Ctx:
    db_path: str = ""
    data_dir: str = ""
    supported: list[str] = []


ctx = _Ctx()


def _handle_init(params: dict[str, Any]) -> dict[str, Any]:
    ctx.db_path = str(params.get("db_path", ""))
    ctx.data_dir = str(params.get("data_dir", ""))
    supported = params.get("supported")
    ctx.supported = list(supported) if isinstance(supported, list) else []
    service.configure(db_path=ctx.db_path, data_dir=ctx.data_dir)
    return {
        "plugin_id": params.get("plugin_id", ""),
        "db_path": ctx.db_path,
        "data_dir": ctx.data_dir,
        "capabilities": [],
    }


def _handle_migrate_db(params: dict[str, Any]) -> dict[str, Any]:
    if ctx.db_path:
        storage.migrate(ctx.db_path)
    return {
        "from_version": params.get("from_version", 0),
        "to_version": params.get("to_version", 1),
    }


def _handle_health_check(params: dict[str, Any]) -> dict[str, Any]:
    return service.health_check()


def _handle_status(params: dict[str, Any]) -> dict[str, Any]:
    return service.status()


def _handle_bridges_list(params: dict[str, Any]) -> list[dict[str, Any]]:
    return service.bridges_list()


def _handle_models(params: dict[str, Any]) -> list[str]:
    return service.models()


def _handle_accounts_list(params: dict[str, Any]) -> list[dict[str, Any]]:
    return service.accounts_list()


def _handle_accounts_add(params: dict[str, Any]) -> dict[str, Any]:
    return service.accounts_add(params)


def _handle_accounts_remove(params: dict[str, Any]) -> dict[str, Any]:
    return service.accounts_remove(params)


def _handle_bridge_start(params: dict[str, Any]) -> dict[str, Any]:
    return service.bridge_start(params)


def _handle_bridge_stop(params: dict[str, Any]) -> dict[str, Any]:
    return service.bridge_stop(params)


def _handle_set_api_key(params: dict[str, Any]) -> dict[str, Any]:
    return service.set_api_key(params)


def main() -> None:
    server = RpcPluginServer()
    server.set_init_handler(_handle_init)
    server.register("_migrate_db", _handle_migrate_db)
    server.register("health_check", _handle_health_check)
    server.register("status", _handle_status)
    server.register("bridges_list", _handle_bridges_list)
    server.register("models", _handle_models)
    server.register("accounts_list", _handle_accounts_list)
    server.register("accounts_add", _handle_accounts_add)
    server.register("accounts_remove", _handle_accounts_remove)
    server.register("bridge_start", _handle_bridge_start)
    server.register("bridge_stop", _handle_bridge_stop)
    server.register("set_api_key", _handle_set_api_key)
    server.serve()


if __name__ == "__main__":
    main()
