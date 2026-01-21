from flask import Flask, request
import os
import json
from pathlib import Path
from datetime import datetime
import requests
import yfinance as yf
import pandas as pd

import time
import random
import hashlib

from pathlib import Path

# =========================
# 專案資料夾位置（一定要有）
# =========================
BASE_DIR = Path(__file__).resolve().parent

# =========================
# 快取設定
# =========================
CACHE_DIR = BASE_DIR / "_cache"   # 會在同資料夾生成 _cache
CACHE_DIR.mkdir(exist_ok=True)

CACHE_TTL_SEC = 300   # 快取 5 分鐘（你可改：60/300/600）
# LINE 查單檔建議 60~300；watchlist 批次建議 300~900

def _cache_path(ticker: str) -> Path:
    # 避免檔名有特殊字元，用 hash 當檔名
    h = hashlib.md5(ticker.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.pkl"

def _is_cache_valid(path: Path) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age <= CACHE_TTL_SEC

def fetch_history_cached(ticker: str) -> pd.DataFrame:
    """
    先讀快取（有效就直接回）
    快取失效才去 yfinance 抓，並存回快取
    內建：rate limit 退避重試
    """
    p = _cache_path(ticker)

    # 1) 快取有效 → 直接用
    if _is_cache_valid(p):
        try:
            return pd.read_pickle(p)
        except Exception:
            # 快取壞掉就當不存在
            pass

    # 2) 快取無效 → 去 yfinance 抓（含退避重試）
    last_err = None
    for attempt in range(6):  # 最多 6 次
        try:
            df = yf.download(
                ticker,
                period="1y",
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
                group_by="column",
            )

            # 成功抓到才寫快取（避免把空 df 寫進去）
            if df is not None and not df.empty:
                try:
                    df.to_pickle(p)
                except Exception:
                    pass

            return df if df is not None else pd.DataFrame()

        except Exception as e:
            last_err = e
            msg = str(e).lower()

            # rate limit / too many requests → 退避等待
            if ("rate" in msg) or ("too many" in msg) or ("429" in msg):
                wait = (2 ** attempt) + random.uniform(0.3, 1.2)
                time.sleep(wait)
                continue

            # 其他錯誤就直接丟出
            raise

    # 重試用完 → 回空 df（讓上層走「抓不到資料」的流程）
    print(f"⚠️ fetch_history_cached 失敗：{ticker}｜{last_err}")
    return pd.DataFrame()

app = Flask(__name__)

# =========================
# LINE 設定
# =========================
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
# 你也可以先暫時直接寫死測試（不建議長期）
# CHANNEL_ACCESS_TOKEN = "你的token"

# =========================
# 檔案位置
# =========================
BASE_DIR = Path(__file__).resolve().parent
NAMES_FILE = BASE_DIR / "names.json"

ETF_NAME_OVERRIDES = {
    "0050.TW": "元大台灣50",
    "006208.TW": "富邦台50",
    "00878.TW": "國泰永續高股息",
    "00919.TW": "群益台灣精選高息",
    "0056.TW": "元大高股息",
    "00757.TW": "統一FANG+",
}

# ====== 你的參數（完全沿用，不改）======
CHEAP_CUTOFF = 0.25
EXPENSIVE_CUTOFF = 0.70
LOOKBACK_DAYS = 120

# =========================
# LINE reply
# =========================
def reply_line(reply_token: str, msg: str):
    if not CHANNEL_ACCESS_TOKEN:
        # 沒設 token 時，至少讓你在 console 看到
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN 未設定，無法回覆。")
        print(msg)
        return 200, "NO_TOKEN"

    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": msg}]
    }
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    print("reply status =",r.status_code)
    print("reply body =",r.text)
    return r.status_code, r.text

# =========================
# names.json（B方案）
# =========================
def load_names_for_reply() -> dict:
    names = {}
    if NAMES_FILE.exists():
        try:
            with open(NAMES_FILE, "r", encoding="utf-8") as f:
                names = json.load(f) or {}
        except Exception:
            names = {}
    # ETF 覆蓋確保好看
    for k, v in ETF_NAME_OVERRIDES.items():
        names[k] = v
    return names

def pretty_code(ticker: str) -> str:
    return ticker.replace(".TW", "").replace(".TWO", "")

def display_name(ticker: str, names: dict) -> str:
    nm = (names or {}).get(ticker, "")
    code = pretty_code(ticker)
    return f"{nm} {code}".strip() if nm else code

def normalize_ticker_input(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    # 去掉常見口語前綴
    for prefix in ["查", "看", "問", "幫我看", "幫我查"]:
        if s.startswith(prefix):
            s = s[len(prefix):].strip()

    # 純數字 4 碼 → .TW
    if s.isdigit() and len(s) == 4:
        return f"{s}.TW"
    # 已經有 .TW
    if ".TW" in s.upper():
        return s.upper()
    return s

def resolve_query_to_ticker(query: str, names: dict):
    q = normalize_ticker_input(query)

    # 1) 代號：2330.TW
    if q.upper().endswith(".TW"):
        return q.upper()

    # 2) 中文名反查（完全相等）
    q2 = q.strip()
    if not q2:
        return None

    for t, nm in (names or {}).items():
        if nm == q2:
            return t

    # 3) 允許「台積電 2330」抓出數字
    digits = "".join([c for c in q2 if c.isdigit()])
    if len(digits) == 4:
        return f"{digits}.TW"

    return None

# =========================
# yfinance 抓資料（不改）
# =========================
def fetch_history(ticker: str) -> pd.DataFrame:
    return yf.download(
        ticker,
        period="1y",
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
        group_by="column",
    )

def get_series(df: pd.DataFrame, col_name: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    if col_name in df.columns:
        s = df[col_name]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s.dropna()

    if isinstance(df.columns, pd.MultiIndex):
        try:
            s = df.xs(col_name, axis=1, level=-1)
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            return s.dropna()
        except Exception:
            return pd.Series(dtype=float)

    return pd.Series(dtype=float)

# =========================
# 你的計算（完全不改）
# =========================
def price_level_label(pos: float) -> str:
    if pos <= CHEAP_CUTOFF:
        return "偏便宜"
    elif pos >= EXPENSIVE_CUTOFF:
        return "偏貴"
    else:
        return "普通"

def calc_pos(close: pd.Series):
    if close is None or close.empty or len(close) < 30:
        return None

    price = float(close.iloc[-1])
    low_52 = float(close.min())
    high_52 = float(close.max())

    denom = (high_52 - low_52)
    if denom <= 1e-12:
        pos = 0.5
    else:
        pos = (price - low_52) / denom

    pos = max(0.0, min(1.0, float(pos)))
    tag = price_level_label(pos)
    return {"price": price, "pos": pos, "tag": tag}

def heat_label(ret5: float, vol_ratio):
    if ret5 >= 15:
        return "近期漲幅明顯"
    if ret5 >= 5:
        return "溫和上漲"
    if ret5 <= -10:
        return "近期偏弱"
    return "變動不大"

def calc_watch_score(close: pd.Series, volume):
    if close is None or close.empty or len(close) < 25:
        return None
    if len(close) < 6:
        return None

    ret5 = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1.0) * 100.0

    vol_ratio = None
    if volume is not None and not volume.empty and len(volume) >= 20:
        v_today = float(volume.iloc[-1]) if pd.notna(volume.iloc[-1]) else 0.0
        v_avg20 = float(volume.tail(20).mean()) if pd.notna(volume.tail(20).mean()) else 0.0
        if v_avg20 > 1e-9:
            vol_ratio = v_today / v_avg20

    score = ret5
    if vol_ratio is not None:
        score += min(max(vol_ratio - 1.0, 0.0), 2.0)

    return {"ret5": ret5, "vol_ratio": vol_ratio, "score": score, "heat": heat_label(ret5, vol_ratio)}

# =========================
# 顯示用白話（只改文字）
# =========================
def tag_desc(tag: str) -> str:
    if tag == "偏便宜":
        return "較靠近一年低點"
    if tag == "偏貴":
        return "較靠近一年高點"
    return "大約在中間區"

def volume_word(vol_ratio):
    if vol_ratio is None:
        return "交易狀況：未知"
    if vol_ratio >= 1.5:
        return "交易狀況：變熱"
    if vol_ratio >= 0.8:
        return "交易狀況：正常"
    return "交易狀況：偏少"

def build_single_stock_reply(ticker: str, names: dict) -> str:
    df = fetch_history(ticker)
    if df is None or df.empty:
        return "抓不到這檔的資料，可能代號有誤或暫時抓不到。"

    close = get_series(df, "Close")
    if close.empty:
        return "這檔資料不足（收盤價抓不到）。"

    info = calc_pos(close)
    if info is None:
        return "這檔資料太少，暫時無法計算。"

    vol = get_series(df, "Volume")
    score = calc_watch_score(close.tail(LOOKBACK_DAYS), vol.tail(LOOKBACK_DAYS) if not vol.empty else None)

    title = display_name(ticker, names)
    mmdd = datetime.now().strftime("%m/%d")

    lines = []
    lines.append(f"{title}｜{mmdd}")
    lines.append("")
    lines.append("【價格狀態】")
    lines.append(f"價格：{info['price']:.2f}")
    lines.append(f"狀態：{info['tag']}（{tag_desc(info['tag'])}）")
    lines.append("")
    lines.append("【近期觀察】")
    if score:
        lines.append(f"近期表現：{score['heat']}（近5日 {score['ret5']:+.1f}%）")
        lines.append(volume_word(score["vol_ratio"]))
    else:
        lines.append("近期表現：資料不足")
    lines.append("")
    lines.append("（僅供觀察，非投資建議）")
    return "\n".join(lines)

# =========================
# 核心：處理使用者文字（你原本缺的「成功回覆」在這）
# =========================
def handle_user_text(reply_token: str, user_text: str):
    names = load_names_for_reply()   # 你原本用的
    ticker = resolve_query_to_ticker(user_text, names)

    # 1) 找不到代號/名稱 → 回教學
    if not ticker:
        help_msg = (
            "你可以直接輸入股票代號或名稱：\n"
            "例如：2330 / 2330.TW / 台積電"
        )
        code, text = reply_line(reply_token, help_msg)
        print("reply status =", code)
        print("reply body   =", text)
        return

    # 2) 找到 ticker → 組出單股回覆 → 真的回 LINE
    msg = build_single_stock_reply(ticker, names)

    # 你說「電腦印出來了」就是這段 msg 已經算得出來
    # 但一定要回覆出去：
    code, text = reply_line(reply_token, msg)
    print("reply status =", code)
    print("reply body   =", text)
    return

    msg = build_single_stock_reply(ticker, names)
    reply_line(reply_token, msg)

# =========================
# webhook：LINE 會打這裡
# =========================
@app.route("/callback", methods=["POST"])
def callback():
    data = request.get_json(silent=True)
    if not data or "events" not in data:
        return "OK"

    for event in data["events"]:
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue

        user_text = msg.get("text", "")
        reply_token = event.get("replyToken", "")
        if reply_token:
            handle_user_text(reply_token, user_text)

    return "OK"
print("user_text =",handle_user_text)
# =========================
# run
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))