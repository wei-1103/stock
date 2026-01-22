# stock.py
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List

import requests

from db import (
    init_db,
    list_all_users,
    list_watchlist,
    list_favorites,
    get_enabled_alerts,
    mark_alert_triggered,
)
from core import (
    load_names,
    fetch_history_cached,
    get_series,
    build_watchlist_summary_message,
    build_single_stock_reply,
    display_name,
)

CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
SEND_SLEEP_SEC = float(os.environ.get("SEND_SLEEP_SEC", "0.8"))


def push_line(user_id: str, msg: str):
    if not CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定")
        print(msg)
        return 200, "NO_TOKEN"

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"to": user_id, "messages": [{"type": "text", "text": msg}]}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    return r.status_code, r.text


def get_latest_price(ticker: str) -> float | None:
    df = fetch_history_cached(ticker)
    close = get_series(df, "Close")
    if close is None or close.empty:
        return None
    return float(close.iloc[-1])


def run_daily_push():
    init_db()
    users = list_all_users()
    today = datetime.now().strftime("%Y-%m-%d")

    for uid in users:
        wl = list_watchlist(uid)
        fav = list_favorites(uid)

        # 1) watchlist 摘要 + 十大（你指定：watchlist 內 score 前十）
        if wl:
            names = load_names(wl)
            msg = build_watchlist_summary_message(
                tickers=wl,
                names=names,
                title_prefix="股票觀察摘要",
                include_top10=True
            )
            push_line(uid, msg)

        # 2) 最愛摘要（每天固定推一次：逐檔卡片）
        if fav:
            names_f = load_names(fav)
            lines: List[str] = []
            mmdd = datetime.now().strftime("%m/%d")
            lines.append(f"最愛清單摘要｜{mmdd}")
            lines.append("（僅供觀察，非投資建議）")
            lines.append("")
            for t in fav:
                lines.append(build_single_stock_reply(t, names_f))
                lines.append("\n" + "-" * 22 + "\n")
            push_line(uid, "\n".join(lines).strip())

    # 3) 價位提醒（全用戶一起掃）
    alerts = get_enabled_alerts()
    for a in alerts:
        alert_id = int(a["id"])
        uid = a["user_id"]
        ticker = a["ticker"]
        direction = a["direction"]
        target = float(a["price"])
        last_date = a.get("last_triggered_date")

        # 一天最多提醒一次（避免洗版）
        if last_date == today:
            continue

        price = get_latest_price(ticker)
        if price is None:
            continue

        triggered = False
        if direction == "below" and price <= target:
            triggered = True
            sym = "跌破"
        elif direction == "above" and price >= target:
            triggered = True
            sym = "突破"
        else:
            sym = "觸發"

        if triggered:
            names = load_names([ticker])
            title = display_name(ticker, names)
            msg = (
                f"⏰ 價位提醒\n"
                f"{title}\n"
                f"目前：{price:.2f}\n"
                f"已{sym}：{target:.2f}\n\n"
                f"（僅供觀察，非投資建議）"
            )
            push_line(uid, msg)
            mark_alert_triggered(alert_id, today)


if __name__ == "__main__":
    run_daily_push()