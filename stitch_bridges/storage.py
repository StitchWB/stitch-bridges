"""SQLite store for bridge accounts and settings."""

from __future__ import annotations

import sqlite3
import time
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def migrate(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "create table if not exists bridge_accounts ("
            " id text primary key, credential text not null,"
            " weight integer not null default 1, proxy text not null default '',"
            " meta text not null default '{}', created_at real not null)"
        )
        conn.execute(
            "create table if not exists bridge_settings ("
            " key text primary key, value text not null)"
        )


def list_accounts(db_path: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "select id, credential, weight, proxy, meta, created_at from bridge_accounts"
        ).fetchall()
    return [dict(row) for row in rows]


def add_account(
    db_path: str,
    *,
    account_id: str,
    credential: str,
    weight: int = 1,
    proxy: str = "",
    meta: str = "{}",
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "insert into bridge_accounts (id, credential, weight, proxy, meta, created_at)"
            " values (?, ?, ?, ?, ?, ?)"
            " on conflict(id) do update set credential=excluded.credential,"
            " weight=excluded.weight, proxy=excluded.proxy, meta=excluded.meta",
            (account_id, credential, weight, proxy, meta, time.time()),
        )


def remove_account(db_path: str, account_id: str) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute("delete from bridge_accounts where id = ?", (account_id,))
    return cursor.rowcount > 0


def get_setting(db_path: str, key: str, default: str = "") -> str:
    with _connect(db_path) as conn:
        row = conn.execute(
            "select value from bridge_settings where key = ?", (key,)
        ).fetchone()
    return str(row["value"]) if row else default


def set_setting(db_path: str, key: str, value: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "insert into bridge_settings (key, value) values (?, ?)"
            " on conflict(key) do update set value=excluded.value",
            (key, value),
        )
