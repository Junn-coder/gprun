#!/usr/bin/env python3
"""
Download 2 years of daily bars for all stocks in stock_history_ak into flat_yao/.
Uses Sina (akshare stock_zh_a_daily) as primary source, Eastmoney as fallback.
Output: flat_yao/<symbol>.csv  (one file per stock, resume-safe)
"""

import os
import sys
import time
from datetime import date, timedelta

import akshare as ak
import pandas as pd
from tqdm import tqdm

HERE = os.path.dirname(os.path.abspath(__file__))
HIST_SRC = os.path.join(os.path.dirname(HERE), "ctool", "stock_history_ak")
OUT_DIR = HERE  # flat_yao/

START_DATE = (date.today() - timedelta(days=730)).strftime("%Y%m%d")  # ~2 years
END_DATE = date.today().strftime("%Y%m%d")

COLUMNS = ["Date", "symbol", "Open", "High", "Low", "Close", "Volume"]
_START_DASH = f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}"
_END_DASH = f"{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}"


def sina_symbol(symbol):
    if symbol.startswith("6"):
        return f"sh{symbol}"
    if symbol.startswith(("4", "8")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def fetch_sina(symbol):
    """Primary: Sina daily bars (qfq). Volume shares -> lots."""
    df = ak.stock_zh_a_daily(symbol=sina_symbol(symbol), adjust="qfq")
    if df is None or df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[(df["date"] >= _START_DASH) & (df["date"] <= _END_DASH)]
    if df.empty:
        return None
    out = pd.DataFrame({
        "Date": df["date"], "symbol": symbol,
        "Open": df["open"], "High": df["high"],
        "Low": df["low"], "Close": df["close"],
        "Volume": (df["volume"] / 100).round().astype("int64"),
    })
    return out[COLUMNS].reset_index(drop=True)


def fetch_eastmoney(symbol):
    """Fallback: Eastmoney daily bars. Volume already in lots."""
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily", start_date=START_DATE,
        end_date=END_DATE, adjust="qfq",
    )
    if df is None or df.empty:
        return None
    out = pd.DataFrame({
        "Date": df["日期"], "symbol": symbol,
        "Open": df["开盘"], "High": df["最高"],
        "Low": df["最低"], "Close": df["收盘"],
        "Volume": df["成交量"],
    })
    return out[COLUMNS].reset_index(drop=True)


def download_stock(symbol, retries=3):
    for label, fetch in (("Sina", fetch_sina), ("Eastmoney", fetch_eastmoney)):
        for i in range(retries):
            try:
                out = fetch(symbol)
                if out is not None and not out.empty:
                    return out
                break
            except Exception as e:
                print(f"  ⚠ {symbol} [{label}] attempt {i+1}/{retries}: {e}", flush=True)
                time.sleep(2)
    return None


def load_codes():
    codes = []
    for fn in sorted(os.listdir(HIST_SRC)):
        if fn.endswith(".csv"):
            codes.append(fn.replace(".csv", ""))
    return codes


def main():
    codes = load_codes()
    print(f"Found {len(codes)} stocks to download")
    print(f"Date range: {_START_DASH} → {_END_DASH}")
    print(f"Output: {OUT_DIR}/")
    os.makedirs(OUT_DIR, exist_ok=True)

    ok, fail, skip = 0, 0, 0
    for symbol in tqdm(codes):
        out_file = os.path.join(OUT_DIR, f"{symbol}.csv")
        if os.path.exists(out_file):
            skip += 1
            continue

        df = download_stock(symbol)
        if df is None or df.empty:
            fail += 1
            time.sleep(0.3)
            continue

        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        df = df[df["Date"] <= _END_DASH]
        df.to_csv(out_file, index=False)
        ok += 1
        time.sleep(0.3)

    print(f"\nDone: {ok} ok, {skip} skipped, {fail} failed — {OUT_DIR}")


if __name__ == "__main__":
    main()
