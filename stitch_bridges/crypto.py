"""At-rest encryption for bridge account credentials.

Credentials (session cookies) are the crown jewels of a bridge: a leaked
``token_v2`` is a full account takeover.  Encrypt at rest with the host core's
Fernet key (``TOKEN_ENCRYPTION_KEY``, handed to storage-declaring plugins);
fall back to a plugin-local key file so standalone dev runs still work.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_PREFIX = "fernet:"


def _load_key(data_dir: Path) -> bytes:
    env = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
    if env:
        from cryptography.fernet import Fernet  # noqa: PLC0415

        key = env.encode("utf-8")
        try:
            Fernet(key)
        except ValueError:
            # Not a raw Fernet key (host passes a raw 32-byte secret) -> derive.
            import base64
            import hashlib

            key = base64.urlsafe_b64encode(hashlib.sha256(env.encode()).digest())
        return key

    key_path = data_dir / ".bridge_key"
    if key_path.is_file():
        return key_path.read_bytes()
    from cryptography.fernet import Fernet  # noqa: PLC0415

    key = Fernet.generate_key()
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


def _fernet(data_dir: Path) -> Any:
    from cryptography.fernet import Fernet  # noqa: PLC0415

    return Fernet(_load_key(data_dir))


def encrypt(data_dir: Path, plaintext: str) -> str:
    return _PREFIX + _fernet(data_dir).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(data_dir: Path, value: str) -> str:
    if not value.startswith(_PREFIX):
        return value
    return _fernet(data_dir).decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
