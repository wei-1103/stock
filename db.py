# db.py
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict


def _resolve_db_path() -> str:
    """
    ✅ 免費方案最簡單穩定：
    - 有設定 DB_PATH 就用 DB_PATH
    - 沒設定就用專案目錄 ./bot.db
    """
    p = os.environ.get("DB_PATH", "").strip()
    if p:
        return p
    return str(Path.cwd() / "bot.db")


DB_PATH = _resolve_db_path()


def _connect() -> sqlite3.Connection:
    # timeout 避免偶發的 database is locked（多工/cron 同時碰到時）
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id TEXT NOT NULL,
            ticker  TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, ticker)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            user_id TEXT NOT NULL,
            ticker  TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, ticker)
        )
        """)

        # direction: 'below' / 'above'
        cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            last_triggered_date TEXT,
            created_at TEXT NOT NULL
        )
        """)
        conn.commit()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -------------------------
# watchlist
# -------------------------
def add_watchlist(user_id: str, ticker: str) -> bool:
    init_db()
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO watchlist(user_id, ticker, created_at) VALUES(?,?,?)",
                (user_id, ticker, now_iso())
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def remove_watchlist(user_id: str, ticker: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE user_id=? AND ticker=?", (user_id, ticker))
        conn.commit()
        return cur.rowcount > 0


def list_watchlist(user_id: str) -> List[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id=? ORDER BY created_at ASC",
            (user_id,)
        ).fetchall()
        return [r["ticker"] for r in rows]


# -------------------------
# favorites
# -------------------------
def add_favorite(user_id: str, ticker: str) -> bool:
    init_db()
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO favorites(user_id, ticker, created_at) VALUES(?,?,?)",
                (user_id, ticker, now_iso())
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def remove_favorite(user_id: str, ticker: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM favorites WHERE user_id=? AND ticker=?", (user_id, ticker))
        conn.commit()
        return cur.rowcount > 0


def list_favorites(user_id: str) -> List[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ticker FROM favorites WHERE user_id=? ORDER BY created_at ASC",
            (user_id,)
        ).fetchall()
        return [r["ticker"] for r in rows]


# -------------------------
# alerts
# -------------------------
def add_alert(user_id: str, ticker: str, direction: str, price: float) -> int:
    """
    return alert id
    """
    init_db()
    if direction not in ("below", "above"):
        raise ValueError("direction must be 'below' or 'above'")
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts(user_id, ticker, direction, price, is_enabled, created_at)
            VALUES(?,?,?,?,1,?)
            """,
            (user_id, ticker, direction, float(price), now_iso())
        )
        conn.commit()
        return int(cur.lastrowid)


def disable_alerts_for_ticker(user_id: str, ticker: str) -> int:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE alerts SET is_enabled=0 WHERE user_id=? AND ticker=? AND is_enabled=1",
            (user_id, ticker)
        )
        conn.commit()
        return cur.rowcount


def list_alerts(user_id: str) -> List[Dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, ticker, direction, price, is_enabled, last_triggered_date, created_at
            FROM alerts
            WHERE user_id=?
            ORDER BY created_at ASC
            """,
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_enabled_alerts() -> List[Dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, ticker, direction, price, last_triggered_date
            FROM alerts
            WHERE is_enabled=1
            """
        ).fetchall()
        return [dict(r) for r in rows]


def mark_alert_triggered(alert_id: int, date_yyyy_mm_dd: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE alerts SET last_triggered_date=? WHERE id=?",
            (date_yyyy_mm_dd, alert_id)
        )
        conn.commit()


# -------------------------
# users
# -------------------------
def list_all_users() -> List[str]:
    """
    取所有曾經有 watchlist/favorites/alerts 的 user_id
    """
    init_db()
    with _connect() as conn:
        rows = conn.execute("""
        SELECT user_id FROM watchlist
        UNION
        SELECT user_id FROM favorites
        UNION
        SELECT user_id FROM alerts
        """).fetchall()
        return [r["user_id"] for r in rows]