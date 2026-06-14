#!/usr/bin/env python3
"""
extract_break_samples.py — pick the matched "pure-up turn" episodes and write one
30-day OHLCV file per episode into tool/break_data/. Each file is a training sample.

Window (period=30): 20 "before" days + 10-day up-leg.
    rows  1..20 : the before-window (what the picker would see at decision day D)
    rows 21..30 : the +10% pure-up leg (kept so you can see what "up" looks like)

A day D qualifies (loose pure-up) when, over the next 10 trading days:
    entry = open[D+1], exit = close[D+10]
    net = (exit-entry)/entry*100 >= WIN_PCT  AND  no close sits more than DD_TOL% below entry

Overlapping qualifying days inside one run are collapsed to a single episode (the
earliest D), so each file is a DISTINCT turn, not shifted copies of the same one.

Output: tool/break_data/<code>_<Ddate>.csv  (Ddate = decision day, YYYYMMDD).
All data local. Positives only (the matched pattern).

Usage:
    python extract_break_samples.py                  # period 30, +10%, dd 5%
    python extract_break_samples.py --win 10 --dd 5
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
OUT_DIR = os.path.join(TOOL_DIR, "break_data")

HOLD = 10  # up-leg (fixed)


def main():
    ap = argparse.ArgumentParser(description="Extract matched pure-up turn samples")
    ap.add_argument("--period", type=int, default=30, help="window length; before = period-10 (default 30)")
    ap.add_argument("--win", type=float, default=10.0, help="pure-up bar %% over 10 days (default 10)")
    ap.add_argument("--dd", type=float, default=5.0, help="max %% a close may sit below entry (default 5)")
    args = ap.parse_args()

    before = args.period - HOLD
    if before < 5:
        sys.exit(f"period {args.period} too short (before window = {before})")

    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))
    print(f"Scanning {len(files)} stocks: period={args.period} (before={before}d + {HOLD}d up-leg), "
          f"win=+{args.win:.0f}%, dd<={args.dd:.0f}% ...", file=sys.stderr)

    written = 0
    per_stock = 0
    stocks_with_hits = 0
    for n, fn in enumerate(files, 1):
        if n % 200 == 0:
            print(f"  ... {n}/{len(files)} stocks, {written} samples", file=sys.stderr)
        code = fn[:-4]
        try:
            df = pd.read_csv(os.path.join(HISTORY_DIR, fn), parse_dates=["Date"])
        except Exception:
            continue
        if df.empty or "Close" not in df.columns or len(df) < before + HOLD + 1:
            continue
        df = df.sort_values("Date").reset_index(drop=True)
        o, c = df["Open"], df["Close"]

        entry = o.shift(-1)
        exit_ = c.shift(-HOLD)
        fut_min = pd.concat([c.shift(-i) for i in range(1, HOLD + 1)], axis=1).min(axis=1)
        net = (exit_ - entry) / entry * 100
        dd_ok = fut_min >= entry * (1 - args.dd / 100.0)
        qual = (net >= args.win) & dd_ok & entry.notna() & exit_.notna()

        pos = set(np.flatnonzero(qual.values))
        kept = [i for i in sorted(pos) if i >= before - 1 and (i - 1) not in pos]
        if kept:
            stocks_with_hits += 1
        for i in kept:
            window = df.iloc[i - (before - 1): i + HOLD + 1].copy()   # 30 rows: D-19 .. D+10
            if len(window) != args.period:
                continue
            if "symbol" in window.columns:
                window["symbol"] = code           # restore zero-padded 6-digit code
            ddate = df.iloc[i]["Date"].strftime("%Y%m%d")
            window.to_csv(os.path.join(OUT_DIR, f"{code}_{ddate}.csv"), index=False)
            written += 1

    print("\n" + "=" * 56)
    print(f"MATCHED SAMPLES  period={args.period}  +{args.win:.0f}%/{HOLD}d pure-up (dd<={args.dd:.0f}%)")
    print("=" * 56)
    print(f"samples written .... {written:,}")
    print(f"stocks with >=1 .... {stocks_with_hits:,} / {len(files):,}")
    print(f"folder ............. {OUT_DIR}")
    print(f"each file .......... {args.period} rows (Date,symbol,Open,High,Low,Close,Volume)")


if __name__ == "__main__":
    main()
