#!/usr/bin/env python3
"""
Bulk-download A-share daily bars (forward-adjusted / qfq).

Data sources (ordered by reliability, with automatic fallback):
  1. Sina stock_zh_a_daily      -- primary source (proven stable by cn_stock.py)
  2. Eastmoney stock_zh_a_hist  -- fallback source (often rate-limited; used only when Sina fails)

Input:  hot_a_stocks.csv (columns: symbol,name)
Output: stock_history_ak/<symbol>.csv  -- one file per stock, supports resume

Usage:
    python downa.py
"""

import os
import time
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
from tqdm import tqdm

HERE =os.path.dirname(os.path.abspath(__file__))
STOCK_LIST = os.path.join(HERE, "hot_a_stocks.csv")
HIST_DIR = os.path.join(HERE, "stock_history_ak")

START_DATE = "20260101"
END_DATE = date.today().strftime("%Y%m%d")  # generous fetch ceiling; real cap is the closed-day target

# A-share session closes 15:00 China time; allow a small buffer for data to settle
_CST = ZoneInfo("Asia/Shanghai")
_CLOSE_TIME = dtime(15, 0)

# Unified output schema (both sources are normalized into this set of English columns)
COLUMNS = ["Date", "symbol", "Open", "High", "Low", "Close", "Volume"]
_START_DASH = f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}"
_END_DASH = f"{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}"


def _sina_symbol(symbol):
    """A-share code -> Sina code with exchange prefix."""
    if symbol.startswith("6"):
        return f"sh{symbol}"
    if symbol.startswith(("4", "8")):
        return f"bj{symbol}"  # Beijing Stock Exchange
    return f"sz{symbol}"


def _fetch_sina(symbol):
    """Primary source: Sina daily bars. Volume is in shares; ÷100 to align with Eastmoney's 'lots'."""
    df = ak.stock_zh_a_daily(symbol=_sina_symbol(symbol), adjust="qfq")
    if df is None or df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[(df["date"] >= _START_DASH) & (df["date"] <= _END_DASH)]
    if df.empty:
        return None
    out = pd.DataFrame({
        "Date": df["date"],
        "symbol": symbol,
        "Open": df["open"],
        "High": df["high"],
        "Low": df["low"],
        "Close": df["close"],
        "Volume": (df["volume"] / 100).round().astype("int64"),  # shares -> lots
    })
    return out[COLUMNS].reset_index(drop=True)


def _fetch_eastmoney(symbol):
    """Fallback source: Eastmoney daily bars. Volume is already in 'lots', no conversion needed."""
    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=START_DATE,
        end_date=END_DATE,
        adjust="qfq"  # forward-adjusted, removes the effect of dividends and bonus shares
    )
    if df is None or df.empty:
        return None
    out = pd.DataFrame({
        "Date": df["日期"],
        "symbol": symbol,
        "Open": df["开盘"],
        "High": df["最高"],
        "Low": df["最低"],
        "Close": df["收盘"],
        "Volume": df["成交量"],
    })
    return out[COLUMNS].reset_index(drop=True)


def download_stock(symbol, retries=3):
    """Sina first, fall back to Eastmoney on failure; retry each source `retries` times."""
    for label, fetch in (("Sina", _fetch_sina), ("Eastmoney", _fetch_eastmoney)):
        for i in range(retries):
            try:
                out = fetch(symbol)
                if out is not None and not out.empty:
                    return out
                break  # source reachable but no data, move on to the next source
            except Exception as e:
                print(f"  ⚠ {symbol} [{label}] attempt {i + 1}/{retries} failed: {e}")
                time.sleep(2)
    return None


def _latest_closed_trading_day():
    """Most recent A-share trading day whose session has fully closed (15:00 CST).

    Uses the Sina trading calendar so weekends/holidays are handled correctly. If
    run before today's close, today's (not-yet-final) bar is excluded.
    """
    cal = ak.tool_trade_date_hist_sina()
    days = sorted(pd.to_datetime(cal["trade_date"]).dt.date)
    now = datetime.now(_CST)
    today = now.date()
    elig = [d for d in days if d <= today]
    last = elig[-1]
    if last == today and now.time() < _CLOSE_TIME:
        last = elig[-2]  # today's session hasn't closed yet -> use previous trading day
    return last.strftime("%Y-%m-%d")


def _last_date(file_path):
    """Latest Date ('YYYY-MM-DD') already saved in a file, or None if unreadable."""
    try:
        return pd.read_csv(file_path, usecols=["Date"])["Date"].max()
    except Exception:
        return None


def download_all(stock_df):
    os.makedirs(HIST_DIR, exist_ok=True)
    target = _latest_closed_trading_day()  # 'YYYY-MM-DD' ceiling for this run
    print(f"Updating up to last closed trading day: {target}")

    for _, row in tqdm(stock_df.iterrows(), total=len(stock_df)):
        symbol = row["symbol"]
        file_path = os.path.join(HIST_DIR, f"{symbol}.csv")
        exists = os.path.exists(file_path)
        last = _last_date(file_path) if exists else None

        # already current (or running before close, so target is the previous day) -> skip
        if last is not None and last >= target:
            continue

        df_hist = download_stock(symbol)
        if df_hist is None:
            time.sleep(0.3)
            continue

        # normalize to 'YYYY-MM-DD' strings so comparisons match the on-disk format
        df_hist["Date"] = pd.to_datetime(df_hist["Date"]).dt.strftime("%Y-%m-%d")
        df_hist = df_hist[df_hist["Date"] <= target]      # don't write an unfinished bar
        if last is not None:
            df_hist = df_hist[df_hist["Date"] > last]     # append only the new rows

        if not df_hist.empty:
            # append to existing file (no header); write a fresh file with header
            df_hist.to_csv(file_path, mode="a", header=not exists, index=False)
        time.sleep(0.3)  # polite request, avoid triggering rate limits


def load_stock_list():
    """Read hot_a_stocks.csv; error out if missing (do not fall back to downloading the whole market)."""
    if not os.path.exists(STOCK_LIST):
        raise FileNotFoundError(f"Input file does not exist: {STOCK_LIST}")
    # dtype=str preserves leading zeros like 000001, otherwise it would be parsed as the integer 1
    return pd.read_csv(STOCK_LIST, dtype={"symbol": str})


def main():
    stock_df = load_stock_list()
    download_all(stock_df)
    print(f"\n✅ Done -> {HIST_DIR}")


if __name__ == "__main__":
    main()
