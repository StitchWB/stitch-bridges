"""Sticky curl_cffi sessions for web bridges.

Fingerprint filters (TLS/JA3 + HTTP/2 + header order) reject plain HTTP
clients, so requests must impersonate a real browser.  The impersonation
profile and the User-Agent must stay consistent for an account: mixing
profiles between requests is itself a detection signal, hence the sticky
``Fingerprint`` chosen once per account.
"""

from __future__ import annotations

import random
from typing import Any

_IMPERSONATE_PROFILES = ("chrome131", "chrome136", "chrome142", "chrome146")

_USER_AGENTS = {
    "chrome131": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "chrome136": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "chrome142": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "chrome146": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
}


def pick_fingerprint(seed: int | None = None) -> dict[str, str]:
    """Sticky (impersonate-profile, User-Agent) pair.

    ``seed`` pins the choice deterministically (per account); without it a
    random profile is drawn.
    """
    profile = (
        random.choice(_IMPERSONATE_PROFILES)
        if seed is None
        else _IMPERSONATE_PROFILES[seed % len(_IMPERSONATE_PROFILES)]
    )
    return {"impersonate": profile, "user_agent": _USER_AGENTS[profile]}


def make_session(
    fingerprint: dict[str, str],
    *,
    proxy: str | None = None,
    timeout: float = 60.0,
) -> Any:
    """Create a curl_cffi ``AsyncSession`` bound to *fingerprint*.

    Installed lazily so the package imports without curl_cffi (unit tests,
    metadata tooling); a missing dependency fails here with a clear message.
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "curl_cffi is required for web bridges (pip install curl_cffi)"
        ) from exc

    kwargs: dict[str, Any] = {
        "impersonate": fingerprint["impersonate"],
        "headers": {"User-Agent": fingerprint["user_agent"]},
        "timeout": timeout,
    }
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return AsyncSession(**kwargs)
