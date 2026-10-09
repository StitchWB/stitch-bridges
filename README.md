# Web AI Bridges

A Stitch service plugin (`stitch-bridges`).

## Quick start (dev loop)

> **Prerequisite:** `stitch_plugin_tools` must be importable. Run
> `pip install -e python/` from the repo root first, or run all
> `python -m stitch_plugin_tools` commands from the `python/` dir.

```bash
# 1. Generate a signing keypair (one-time):
python -m stitch_plugin_tools keygen --out keys/

# 2. Sign the package:
python -m stitch_plugin_tools sign . --key keys/private.key

# 3. Dev-install to plugins-local:
python -m stitch_plugin_tools dev-install .

# 4. Start Stitch with STITCH_DEV_MODE=1 (allows unsigned dev packages):
STITCH_DEV_MODE=1 python -m stitch_backend
```

## Commands

| Command | Readonly | Description |
|--------|----------|-------------|
| `health_check` | yes | Health check — returns `{pong: true}` |
| `echo` | yes | Echoes back the `text` param |

## Testing

`new` generates `tests/test_plugin_protocol.py` — a protocol smoke test
that spawns the plugin via the `stitch_plugin_testing` harness and
exercises init → ping → echo → close.

```bash
# Run the generated test (from the package root):
python -m pytest tests/ -q --timeout=60

# Or via the stitch_plugin_tools test command:
python -m stitch_plugin_tools test .
```

> **Prerequisite:** `stitch_plugin_testing` must be importable. Run
> `pip install -e python/` from the repo root first (same as the
> `stitch_plugin_tools` prerequisite above). If the harness is not
> importable at test runtime, the generated test skips with a clear
> message rather than failing.

## Layout

```
stitch-bridges/
├── plugin.json              # v2 manifest (kind=service)
├── README.md
├── tests/
│   └── test_plugin_protocol.py  # generated protocol smoke test
└── stitch_bridges/
    ├── __init__.py
    ├── __main__.py           # RPC entry (RpcPluginServer)
    ├── service.py             # domain logic (handlers delegate here)
    └── storage.py            # SQLite helper
```

## Publishing

```bash
# Sign + zip + POST to the server:
python -m stitch_plugin_tools publish . \
    --server-url http://localhost:8900 \
    --admin-key <key> \
    --key keys/private.key
```

For community submission, see `docs/service-plugins.md`.
