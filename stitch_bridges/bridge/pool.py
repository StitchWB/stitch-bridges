"""Account pool for web bridges.

Many consumer services tolerate a single session poorly (rate limits, bans),
so a bridge rotates over several accounts.  The pool keeps one sticky session
per account, parks an account after a failure (cooldown), and caps how many
requests an account may run at once.

Pattern follows ``gpt4free``'s ``ProviderCircuitBreaker`` (cooldown dict) and
``one-api``'s channel status/weight selection.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .session import make_session, pick_fingerprint


@dataclass
class Account:
    """One upstream account: a session, its budget and its health."""

    id: str
    credential: str
    weight: int = 1
    proxy: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    fingerprint: dict[str, str] = field(default_factory=lambda: pick_fingerprint())
    cooldown_until: float = 0.0
    in_flight: int = 0
    total_calls: int = 0
    total_errors: int = 0
    last_error: str = ""
    session: Any = None

    def available(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) >= self.cooldown_until

    def session_or_create(self) -> Any:
        if self.session is None:
            self.session = make_session(self.fingerprint, proxy=self.proxy)
        return self.session


class AccountPool:
    """Weighted, cooldown-aware selection over accounts."""

    def __init__(
        self,
        accounts: list[Account] | None = None,
        *,
        cooldown_s: float = 120.0,
        max_concurrency: int = 1,
    ) -> None:
        self._accounts: list[Account] = list(accounts or [])
        self._cooldown_s = cooldown_s
        self._max_concurrency = max_concurrency
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._accounts)

    def add(self, account: Account) -> None:
        with self._lock:
            self._accounts = [a for a in self._accounts if a.id != account.id]
            self._accounts.append(account)

    def remove(self, account_id: str) -> bool:
        with self._lock:
            before = len(self._accounts)
            self._accounts = [a for a in self._accounts if a.id != account_id]
            return len(self._accounts) != before

    def pick(self) -> Account | None:
        """Choose a ready account by weight; ``None`` when all are parked/busy."""
        now = time.monotonic()
        with self._lock:
            ready = [
                a
                for a in self._accounts
                if a.available(now) and a.in_flight < self._max_concurrency
            ]
            if not ready:
                return None
            weights = [max(1, a.weight) for a in ready]
            account = random.choices(ready, weights=weights, k=1)[0]
            account.in_flight += 1
            account.total_calls += 1
            return account

    def release(self, account: Account, *, error: str = "") -> None:
        """Return an account after a call; a failure parks it for the cooldown."""
        with self._lock:
            account.in_flight = max(0, account.in_flight - 1)
            if error:
                account.total_errors += 1
                account.last_error = error[:500]
                account.cooldown_until = time.monotonic() + self._cooldown_s

    def stats(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "id": a.id,
                    "weight": a.weight,
                    "available": a.available(now),
                    "cooldown_s": max(0, round(a.cooldown_until - now)),
                    "in_flight": a.in_flight,
                    "calls": a.total_calls,
                    "errors": a.total_errors,
                    "last_error": a.last_error,
                }
                for a in self._accounts
            ]
