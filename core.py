# core.py
from __future__ import annotations

import os
import time
import json
import random
import hashlib
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

import requests
import yfinance as yf
import pandas as pd


# =========================
# 基本設定
# =========================
CHEAP_CUTOFF = float(os.environ.get("CHEAP_CUTOFF", "0.25"))
EXPENSIVE_CUTOFF = float(os.environ.get("EXPENSIVE_CUTOFF", "0.70"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "120"))

STATUS_SHOW_MAX = int(os.environ.get("STATUS_SHOW_MAX", "8"))
TOP_N_SCORE = int(os.environ.get("TOP_N_SCORE", "10"))

CACHE_TTL_SEC = int(os.environ.get("CACHE_TTL_SEC", "300"))  # 5 min default

# =========================
# 路徑設定（可寫才用；不可寫就 fallback）
# =========================
def _ensure_writable_dir(p: Path) -> Path:
    """
    確保資料夾存在且可寫；不可寫就丟例外
    """
    p.mkdir(parents=True, exist_ok=True)
    test = p / ".write_test"
    test.write_text("ok", encoding="utf-8")
    test.unlink(missing_ok=True)
    return p

def _resolve_data_dir() -> Path:
    # 依序嘗試：env DATA_DIR -> /var/data -> /tmp/data
    candidates = [
        os.environ.get("DATA_DIR", "").strip(),
        "/var/data",
        "/tmp/data",
    ]
    for c in candidates:
        if not c:
            continue
        try:
            return _ensure_writable_dir(Path(c))
        except Exception:
            pass

    # 最後保底：專案底下 data（不一定可寫，但再試一次）
    fallback = Path.cwd() / "data"
    try:
        return _ensure_writable_dir(fallback)
    except Exception:
        # 真的都不行就回 /tmp
        return Path("/tmp")

DATA_DIR = _resolve_data_dir()

CACHE_DIR = Path(os.environ.get("CACHE_DIR", str(DATA_DIR / "_cache")))
try:
    _ensure_writable_dir(CACHE_DIR)
except Exception:
    # cache 失敗就退回 /tmp/cache
    CACHE_DIR = _ensure_writable_dir(Path("/tmp/data/_cache"))

NAMES_FILE = Path(os.environ.get("NAMES_FILE", str(DATA_DIR / "names.json")))
# 確保 names.json 的父層存在（避免寫入時炸掉）
try:
    NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
ETF_NAME_OVERRIDES = {
    "0050.TW": "元大台灣50",
    "006208.TW": "富邦台50",
    "00878.TW": "國泰永續高股息",
    "00919.TW": "群益台灣精選高息",
    "0056.TW": "元大高股息",
    "00757.TW": "統一FANG+",
}


# =========================
# 小工具
# =========================
def pretty_code(ticker: str) -> str:
    return ticker.replace(".TW", "").replace(".TWO", "")


def display_name(ticker: str, names: Dict[str, str]) -> str:
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

    if s.isdigit() and len(s) == 4:
        return f"{s}.TW"
    if ".TW" in s.upper():
        return s.upper()
    return s


def resolve_query_to_ticker(query: str, names: Dict[str, str]) -> Optional[str]:
    """
    使用者輸入：
    - 2330 / 2330.TW → 轉 ticker
    - 台積電 → 用 names.json 反查 ticker（完全相等）
    - 台積電2330 → 抓出 4 碼
    """
    q = normalize_ticker_input(query)

    if q.upper().endswith(".TW"):
        return q.upper()

    q2 = q.strip()
    if not q2:
        return None

    # 中文名反查
    for t, nm in (names or {}).items():
        if nm == q2:
            return t

    digits = "".join([c for c in q2 if c.isdigit()])
    if len(digits) == 4:
        return f"{digits}.TW"

    return None


# =========================
# 快取（yfinance）
# =========================
def _cache_path(ticker: str) -> Path:
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
            pass

    # 2) 快取無效 → yfinance（含退避重試）
    last_err = None
    for attempt in range(6):
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
            if df is not None and not df.empty:
                try:
                    df.to_pickle(p)
                except Exception:
                    pass
            return df if df is not None else pd.DataFrame()

        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if ("rate" in msg) or ("too many" in msg) or ("429" in msg):
                wait = (2 ** attempt) + random.uniform(0.3, 1.2)
                time.sleep(wait)
                continue
            raise

    print(f"⚠️ fetch_history_cached 失敗：{ticker}｜{last_err}")
    return pd.DataFrame()


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
# names.json（公司簡稱）
# =========================
def _download_csv(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return pd.read_csv(pd.io.common.BytesIO(r.content), encoding="utf-8-sig")


def _build_names_from_mops() -> Dict[str, str]:
    """
    從公開資訊觀測站 open data 抓上市/上櫃公司基本資料
    """
    urls = [
        "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",  # 上市
        "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",  # 上櫃
    ]
    mapping: Dict[str, str] = {}
    for url in urls:
        df = _download_csv(url)
        code_col = "公司代號" if "公司代號" in df.columns else None
        abbr_col = "公司簡稱" if "公司簡稱" in df.columns else None
        name_col = "公司名稱" if "公司名稱" in df.columns else None
        if not code_col:
            continue

        for _, row in df.iterrows():
            code = str(row.get(code_col, "")).strip()
            if not code or not code.isdigit():
                continue

            abbr = ""
            if abbr_col and pd.notna(row.get(abbr_col)):
                abbr = str(row.get(abbr_col)).strip()
            if not abbr and name_col and pd.notna(row.get(name_col)):
                abbr = str(row.get(name_col)).strip()
            if not abbr:
                continue

            mapping[f"{code}.TW"] = abbr
    return mapping


def load_names(tickers: Optional[List[str]] = None) -> Dict[str, str]:
    """
    讀 names.json；沒有就自動抓一次並存檔。
    最後永遠覆蓋 ETF 名稱。
    """
    names: Dict[str, str] = {}

    if NAMES_FILE.exists():
        try:
            names = json.loads(NAMES_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            names = {}

    if not names:
        try:
            all_map = _build_names_from_mops()
            if tickers:
                names = {t: all_map.get(t, "") for t in tickers if all_map.get(t)}
            else:
                names = all_map
            # 覆蓋 ETF
            for k, v in ETF_NAME_OVERRIDES.items():
                names[k] = v
            if names:
                NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            names = {}

    # 永遠覆蓋 ETF
    for k, v in ETF_NAME_OVERRIDES.items():
        names[k] = v
    return names


# =========================
# 計算
# =========================
def price_level_label(pos: float) -> str:
    if pos <= CHEAP_CUTOFF:
        return "偏便宜"
    elif pos >= EXPENSIVE_CUTOFF:
        return "偏貴"
    else:
        return "普通"


def calc_pos(close: pd.Series) -> Optional[Dict]:
    if close is None or close.empty or len(close) < 30:
        return None

    price = float(close.iloc[-1])
    low_52 = float(close.min())
    high_52 = float(close.max())

    denom = (high_52 - low_52)
    pos = 0.5 if denom <= 1e-12 else (price - low_52) / denom
    pos = max(0.0, min(1.0, float(pos)))
    tag = price_level_label(pos)
    return {"price": price, "pos": pos, "tag": tag}


def heat_label(ret5: float, vol_ratio: Optional[float]) -> str:
    if ret5 >= 15:
        return "近期漲幅明顯"
    if ret5 >= 5:
        return "溫和上漲"
    if ret5 <= -10:
        return "近期偏弱"
    return "變動不大"


def calc_watch_score(close: pd.Series, volume: Optional[pd.Series]) -> Optional[Dict]:
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


def tag_desc(tag: str) -> str:
    if tag == "偏便宜":
        return "接近一年低點"
    if tag == "偏貴":
        return "接近一年高點"
    return "價格在中間區"


def volume_word(vol_ratio: Optional[float]) -> str:
    if vol_ratio is None:
        return "交易狀況：未知"
    if vol_ratio >= 1.5:
        return "交易狀況：變熱"
    if vol_ratio >= 0.8:
        return "交易狀況：正常"
    return "交易狀況：偏少"


# =========================
# 產出訊息模板
# =========================
def build_single_stock_reply(ticker: str, names: Dict[str, str]) -> str:
    df = fetch_history_cached(ticker)
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

    lines: List[str] = []
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


def build_watchlist_summary_message(
    tickers: List[str],
    names: Dict[str, str],
    title_prefix: str = "股票觀察摘要",
    include_top10: bool = True,
) -> str:
    mmdd = datetime.now().strftime("%m/%d")

    status_data = []
    rank_data = []
    failed = []

    for t in tickers:
        try:
            df = fetch_history_cached(t)
            if df is None or df.empty:
                failed.append({"ticker": t, "reason": "抓不到資料"})
                continue

            close = get_series(df, "Close")
            if close.empty:
                failed.append({"ticker": t, "reason": "收盤價資料不足"})
                continue

            info = calc_pos(close)
            if info is None:
                failed.append({"ticker": t, "reason": "資料太少"})
                continue

            status_data.append({"ticker": t, "price": info["price"], "pos": info["pos"], "tag": info["tag"]})

            vol = get_series(df, "Volume")
            score_info = calc_watch_score(
                close.tail(LOOKBACK_DAYS),
                vol.tail(LOOKBACK_DAYS) if not vol.empty else None
            )
            if score_info is not None:
                rank_data.append({
                    "ticker": t,
                    "ret5": score_info["ret5"],
                    "vol_ratio": score_info["vol_ratio"],
                    "score": score_info["score"],
                    "heat": score_info["heat"],
                })

        except Exception as e:
            failed.append({"ticker": t, "reason": f"程式錯誤：{e.__class__.__name__}"})

        time.sleep(0.03)

    # 價格狀態：pos 越小越便宜
    status_data.sort(key=lambda x: x["pos"])
    status_rows = status_data[:STATUS_SHOW_MAX]

    # 十大：score 前十（你指定）
    rank_data.sort(key=lambda x: x["score"], reverse=True)
    rank_rows = rank_data[:TOP_N_SCORE] if include_top10 else []

    lines: List[str] = []
    lines.append(f"{title_prefix}｜{mmdd}")
    lines.append("（僅供觀察，非投資建議）")
    lines.append("")

    lines.append("【價格狀態】")
    lines.append("（與過去一年相比）")
    lines.append("")
    if status_rows:
        for r in status_rows:
            title = display_name(r["ticker"], names)
            lines.append(f"{title}")
            lines.append(f"價格：{r['price']:.2f}")
            lines.append(f"狀態：{r['tag']}（{tag_desc(r['tag'])}）")
            lines.append("")
    else:
        lines.append("今天沒有抓到可用資料")
        lines.append("")

    if include_top10:
        lines.append("【十大排名（score）】")
        lines.append("（watchlist 內 score 前十）")
        if rank_rows:
            for i, r in enumerate(rank_rows, 1):
                title = display_name(r["ticker"], names)
                lines.append(f"{i}. {title}｜{r['heat']}（近5日 {r['ret5']:+.1f}%）")
                lines.append(f"   {volume_word(r['vol_ratio'])}")
                lines.append("")
        else:
            lines.append("資料不足，今天排不出排行")

    if failed:
        lines.append("")
        lines.append("【抓不到資料】（代號可能有誤或暫時抓不到）")
        for r in failed[:10]:
            lines.append(f"- {pretty_code(r['ticker'])}：{r['reason']}")
        if len(failed) > 10:
            lines.append(f"（另外還有 {len(failed)-10} 檔略過）")

    return "\n".join(lines)


def build_market_daily_message(names: Dict[str, str]) -> str:
    """
    每日股市：用 yfinance 抓大盤（^TWII）簡單摘要
    """
    mmdd = datetime.now().strftime("%m/%d")
    lines: List[str] = []
    lines.append(f"每日股市｜{mmdd}")
    lines.append("（僅供觀察，非投資建議）")
    lines.append("")

    # 台灣加權指數（通常 yfinance 可用 ^TWII）
    idx = "^TWII"
    df = fetch_history_cached(idx)
    close = get_series(df, "Close")
    if close is None or close.empty or len(close) < 6:
        lines.append("大盤：資料不足（^TWII 抓不到時偶爾會發生）")
        return "\n".join(lines)

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) >= 2 else last
    chg = last - prev
    chg_pct = (chg / prev * 100.0) if prev != 0 else 0.0

    ret5 = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1.0) * 100.0

    lines.append(f"加權指數：{last:,.2f}")
    lines.append(f"日變動：{chg:+.2f}（{chg_pct:+.2f}%）")
    lines.append(f"近5日：{ret5:+.2f}%")
    lines.append("")
    lines.append("指令：輸入『十大』看你關注清單 score 前十；輸入代號查單檔。")
    return "\n".join(lines)