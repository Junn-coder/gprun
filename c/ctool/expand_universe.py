#!/usr/bin/env python3
"""
Expand stock_history_ak from 948 to full 5,175 A-share universe.
Reads all_a_stocks.csv, skips existing CSVs, downloads missing via Sina (akshare).
Resume-safe: re-running picks up where it left off.
"""

import os
import sys
import time
from datetime import date

import akshare as ak
import pandas as pd
from tqdm import tqdm

HERE = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(HERE, "stock_history_ak")
STOCK_LIST = os.path.join(HERE, "all_a_stocks.csv")

START_DATE = (date.today().replace(year=date.today().year - 2)).strftime("%Y%m%d")
END_DATE = date.today().strftime("%Y%m%d")
_START_DASH = f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}"
_END_DASH = f"{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}"

COLUMNS = ["Date", "symbol", "Open", "High", "Low", "Close", "Volume"]


def sina_symbol(code):
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def fetch_sina(code):
    df = ak.stock_zh_a_daily(symbol=sina_symbol(code), adjust="qfq")
    if df is None or df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[(df["date"] >= _START_DASH) & (df["date"] <= _END_DASH)]
    if df.empty:
        return None
    out = pd.DataFrame({
        "Date": df["date"], "symbol": code,
        "Open": df["open"], "High": df["high"],
        "Low": df["low"], "Close": df["close"],
        "Volume": (df["volume"] / 100).round().astype("int64"),
    })
    return out[COLUMNS].reset_index(drop=True)


def fetch_eastmoney(code):
    df = ak.stock_zh_a_hist(
        symbol=code, period="daily", start_date=START_DATE,
        end_date=END_DATE, adjust="qfq",
    )
    if df is None or df.empty:
        return None
    out = pd.DataFrame({
        "Date": df["日期"], "symbol": code,
        "Open": df["开盘"], "High": df["最高"],
        "Low": df["最低"], "Close": df["收盘"],
        "Volume": df["成交量"],
    })
    return out[COLUMNS].reset_index(drop=True)


def download_one(code, retries=3):
    for label, fetch in (("Sina", fetch_sina), ("Eastmoney", fetch_eastmoney)):
        for i in range(retries):
            try:
                out = fetch(code)
                if out is not None and not out.empty:
                    return out
                break
            except Exception as e:
                print(f"  ⚠ {code} [{label}] attempt {i+1}/{retries}: {e}", flush=True)
                time.sleep(2)
    return None


def main():
    # Load all codes
    all_df = pd.read_csv(STOCK_LIST, dtype={"symbol": str})
    all_codes = all_df["symbol"].tolist()
    print(f"Full universe: {len(all_codes)} stocks")

    # Find missing
    existing = set(fn.replace(".csv", "") for fn in os.listdir(HIST_DIR) if fn.endswith(".csv"))
    missing = [c for c in all_codes if c not in existing]
    print(f"Already have: {len(existing)} | Missing: {len(missing)}")
    print(f"Date range: {_START_DASH} → {_END_DASH}")

    if not missing:
        print("Nothing to do.")
        return

    os.makedirs(HIST_DIR, exist_ok=True)
    ok, fail, skip = 0, 0, 0

    for code in tqdm(missing):
        out_file = os.path.join(HIST_DIR, f"{code}.csv")

        df = download_one(code)
        if df is None or df.empty:
            fail += 1
            time.sleep(0.3)
            continue

        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        df = df[df["Date"] <= _END_DASH]
        df.to_csv(out_file, index=False)
        ok += 1
        time.sleep(0.3)

    print(f"\nDone: {ok} ok, {fail} failed, {skip} skipped — {HIST_DIR}")


if __name__ == "__main__":
    main()
