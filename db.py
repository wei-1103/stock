# db.py
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# =========================
# Path resolution (safe)
# =========================
def _is_writable_dir(dir_path: Path) -> bool:
    """
    檢查目錄是否可寫：
    - 目錄不存在：嘗試建立
    - 可建立 / 可寫入測試檔 => True
    """
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        test_file = dir_path / ".write_test"
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
        try:
            test_file.unlink()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _resolve_db_path() -> str:
    """
    依序嘗試：
    1) env DB_PATH（你設定的）
    2) /var/data/bot.db（你原本的預設）
    3) /tmp/data/bot.db（Render 免費方案保底可寫）
    """
    candidates = [
        os.environ.get("DB_PATH", "").strip(),
        "/var/data/bot.db",
        "/tmp/data/bot.db",
    ]

    for p in candidates:
        if not p:
            continue
        path = Path(p)
        parent = path.parent
        if _is_writable_dir(parent):
            return str(path)

    # 最後保底：當前目錄（理論上幾乎不會走到這）
    local_fallback = Path.cwd() / "bot.db"
    _is_writable_dir(local_fallback.parent)
    return str(local_fallback)


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