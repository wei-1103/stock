# app.py
from __future__ import annotations

import os
import re
from typing import Optional, Tuple, List, Dict

from flask import Flask, request

import requests

from db import (
    init_db,
    add_watchlist, remove_watchlist, list_watchlist,
    add_favorite, remove_favorite, list_favorites,
    add_alert, disable_alerts_for_ticker, list_alerts,
)
from core import (
    load_names,
    resolve_query_to_ticker,
    build_single_stock_reply,
    build_watchlist_summary_message,
    build_market_daily_message,
)

app = Flask(__name__)
init_db()

CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")  # 你之後要驗簽再用


# =========================
# LINE reply/push
# =========================
def reply_line(reply_token: str, msg: str) -> Tuple[int, str]:
    if not CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定")
        print(msg)
        return 200, "NO_TOKEN"

    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": msg}]}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    return r.status_code, r.text


# =========================
# 指令解析
# =========================
HELP_TEXT = (
    "指令大全：\n"
    "1) 查單檔：直接輸入 2330 或 台積電\n"
    "2) 關注：新增 2330｜刪除 2330｜清單\n"
    "3) 最愛：最愛新增 2330｜最愛刪除 2330｜最愛清單\n"
    "4) 十大：十大（watchlist score 前十）\n"
    "5) 每日：每日（大盤摘要）\n"
    "6) 提醒：提醒 2330 600（預設跌破）\n"
    "   或：提醒 2330 > 600 / 提醒 2330 < 600\n"
    "   取消：取消提醒 2330\n"
    "（僅供觀察，非投資建議）"
)

RE_ADD = re.compile(r"^\s*新增\s+(.+)\s*$")
RE_DEL = re.compile(r"^\s*刪除\s+(.+)\s*$")
RE_LIST = re.compile(r"^\s*清單\s*$")

RE_FAV_ADD = re.compile(r"^\s*最愛新增\s+(.+)\s*$")
RE_FAV_DEL = re.compile(r"^\s*最愛刪除\s+(.+)\s*$")
RE_FAV_LIST = re.compile(r"^\s*最愛清單\s*$")

RE_TOP10 = re.compile(r"^\s*十大\s*$")
RE_DAILY = re.compile(r"^\s*每日\s*$")

RE_ALERT = re.compile(r"^\s*提醒\s+(.+?)\s+(.+)\s*$")
RE_ALERT_CANCEL = re.compile(r"^\s*取消提醒\s+(.+)\s*$")

RE_HELP = re.compile(r"^\s*(help|幫助|指令)\s*$", re.IGNORECASE)


def parse_alert_price(s: str) -> Tuple[str, float]:
    """
    支援：
    - "600" -> below 600
    - "< 600" / "＜600" -> below
    - "> 600" / "＞600" -> above
    """
    raw = s.strip().replace("＜", "<").replace("＞", ">").replace(" ", "")
    direction = "below"
    if raw.startswith(">"):
        direction = "above"
        raw = raw[1:]
    elif raw.startswith("<"):
        direction = "below"
        raw = raw[1:]

    price = float(raw)
    return direction, price


def ensure_ticker(q: str, names: Dict[str, str]) -> Optional[str]:
    t = resolve_query_to_ticker(q, names)
    # 沒找到就試著把純 4 碼補 .TW
    if not t:
        q2 = (q or "").strip()
        if q2.isdigit() and len(q2) == 4:
            t = f"{q2}.TW"
    return t


def handle_text(user_id: str, text: str, reply_token: str) -> None:
    text = (text or "").strip()
    if not text:
        reply_line(reply_token, HELP_TEXT)
        return

    # names（用來中文名反查）
    names = load_names()

    # 幫助
    if RE_HELP.match(text):
        reply_line(reply_token, HELP_TEXT)
        return

    # 清單
    if RE_LIST.match(text):
        wl = list_watchlist(user_id)
        if not wl:
            reply_line(reply_token, "你的關注清單是空的。\n用法：新增 2330\n" + "（僅供觀察，非投資建議）")
            return
        msg = "你的關注清單：\n" + "\n".join([f"- {t}" for t in wl])
        msg += "\n\n（僅供觀察，非投資建議）"
        reply_line(reply_token, msg)
        return

    # 新增 / 刪除 watchlist
    m = RE_ADD.match(text)
    if m:
        q = m.group(1)
        t = ensure_ticker(q, names)
        if not t:
            reply_line(reply_token, "我看不懂你要新增哪一檔🥲\n例：新增 2330")
            return
        ok = add_watchlist(user_id, t)
        reply_line(reply_token, f"{'✅ 已新增' if ok else '（已在清單中）'}：{t}")
        return

    m = RE_DEL.match(text)
    if m:
        q = m.group(1)
        t = ensure_ticker(q, names)
        if not t:
            reply_line(reply_token, "我看不懂你要刪除哪一檔🥲\n例：刪除 2330")
            return
        ok = remove_watchlist(user_id, t)
        reply_line(reply_token, f"{'✅ 已刪除' if ok else '（清單中沒有這檔）'}：{t}")
        return

    # 最愛清單
    if RE_FAV_LIST.match(text):
        fav = list_favorites(user_id)
        if not fav:
            reply_line(reply_token, "你的最愛清單是空的。\n用法：最愛新增 2330\n" + "（僅供觀察，非投資建議）")
            return
        msg = "你的最愛清單：\n" + "\n".join([f"- {t}" for t in fav])
        msg += "\n\n（最愛會每天固定推播摘要；僅供觀察，非投資建議）"
        reply_line(reply_token, msg)
        return

    # 最愛新增/刪除
    m = RE_FAV_ADD.match(text)
    if m:
        q = m.group(1)
        t = ensure_ticker(q, names)
        if not t:
            reply_line(reply_token, "我看不懂你要加入最愛哪一檔🥲\n例：最愛新增 2330")
            return
        ok = add_favorite(user_id, t)
        reply_line(reply_token, f"{'💛 已加入最愛' if ok else '（已在最愛中）'}：{t}")
        return

    m = RE_FAV_DEL.match(text)
    if m:
        q = m.group(1)
        t = ensure_ticker(q, names)
        if not t:
            reply_line(reply_token, "我看不懂你要移除最愛哪一檔🥲\n例：最愛刪除 2330")
            return
        ok = remove_favorite(user_id, t)
        reply_line(reply_token, f"{'🖤 已移除最愛' if ok else '（最愛中沒有這檔）'}：{t}")
        return

    # 十大（watchlist score 前十）
    if RE_TOP10.match(text):
        wl = list_watchlist(user_id)
        if not wl:
            reply_line(reply_token, "你的關注清單是空的，無法計算十大。\n先用：新增 2330")
            return
        names2 = load_names(wl)
        msg = build_watchlist_summary_message(wl, names2, title_prefix="十大排名", include_top10=True)
        reply_line(reply_token, msg)
        return

    # 每日股市
    if RE_DAILY.match(text):
        msg = build_market_daily_message(load_names())
        reply_line(reply_token, msg)
        return

    # 提醒設定
    m = RE_ALERT.match(text)
    if m:
        q = m.group(1).strip()
        p = m.group(2).strip()
        t = ensure_ticker(q, names)
        if not t:
            reply_line(reply_token, "我看不懂你要提醒哪一檔🥲\n例：提醒 2330 600")
            return
        try:
            direction, price = parse_alert_price(p)
        except Exception:
            reply_line(reply_token, "提醒價格格式不對🥲\n例：提醒 2330 600 或 提醒 2330 < 600")
            return
        alert_id = add_alert(user_id, t, direction, price)
        sym = "跌破" if direction == "below" else "突破"
        reply_line(reply_token, f"✅ 已設定提醒：{t} {sym} {price}\n（提醒會在每日排程檢查）")
        return

    m = RE_ALERT_CANCEL.match(text)
    if m:
        q = m.group(1).strip()
        t = ensure_ticker(q, names)
        if not t:
            reply_line(reply_token, "我看不懂你要取消哪一檔的提醒🥲\n例：取消提醒 2330")
            return
        n = disable_alerts_for_ticker(user_id, t)
        reply_line(reply_token, f"✅ 已取消提醒：{t}（共停用 {n} 條）")
        return

    # 其他：當作查單檔
    t = resolve_query_to_ticker(text, names)
    if not t:
        # 如果是純數字 4 碼
        if text.isdigit() and len(text) == 4:
            t = f"{text}.TW"

    if not t:
        reply_line(reply_token, "我看不懂你的指令🥲\n\n" + HELP_TEXT)
        return

    msg = build_single_stock_reply(t, names)
    reply_line(reply_token, msg)


# =========================
# Webhook
# =========================
@app.route("/", methods=["GET"])
def home():
    return "OK"


@app.route("/callback", methods=["GET", "POST"])
def callback():
    if request.method == "GET":
        return "OK"

    data = request.get_json(silent=True)
    if not data or "events" not in data:
        return "OK"

    for event in data["events"]:
        etype = event.get("type")

        # 來源 userId（重要：每個人都會有自己的 watchlist/fav）
        source = event.get("source", {}) or {}
        user_id = source.get("userId", "")

        reply_token = event.get("replyToken", "")

        # 加好友 welcome（follow 事件）
        if etype == "follow":
            if reply_token:
                welcome = (
                    "感謝您的加入 (´-ω- )💨\n\n"
                    "這是自動回覆機器人：\n"
                    "- 直接輸入股票代號：2330\n"
                    "- 或輸入：清單 / 新增 2330 / 刪除 2330 / 十大 / 每日\n"
                    "- 最愛：最愛新增 2330（每天會推播摘要）\n"
                    "- 提醒：提醒 2330 600（預設跌破）\n\n"
                    "（僅供觀察，非投資建議）"
                )
                reply_line(reply_token, welcome)
            continue

        # 訊息事件
        if etype != "message":
            continue

        msg = event.get("message", {}) or {}
        if msg.get("type") != "text":
            if reply_token:
                reply_line(reply_token, "目前只支援文字訊息喔～\n\n" + HELP_TEXT)
            continue

        text = msg.get("text", "")
        if reply_token and user_id:
            handle_text(user_id, text, reply_token)
        elif reply_token:
            reply_line(reply_token, "我抓不到你的 userId，可能是 webhook 格式不完整。")

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
