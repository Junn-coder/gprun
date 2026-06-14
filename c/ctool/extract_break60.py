#!/usr/bin/env python3
"""
extract_break60.py — pick 60-day "flat base -> steady +15% climb" episodes and write one
60-day OHLCV file per episode into tool/break_data_60/.

Window (60 days): 30-day flat base + 30-day up-leg.  Decision day D = end of the base.
    rows  1..30 : the FLAT base (what the picker sees at day D) — features
    rows 31..60 : the +15% up-leg (the outcome to predict)

Qualifies when ALL hold:
    base  : net change over days 1..30, (close[D]-close[D-29])/close[D-29]*100, in [-4, +4]
    win   : entry = open[D+1], exit = close[D+30], net = (exit-entry)/entry*100 >= +15
    trend : the 30 up-leg closes fit a straight line with POSITIVE slope and R^2 >= 0.5
            (Pearson r >= 0.707) — a steady climb, not a spike or a crash-and-rebound

Decision day restricted to 2025 + 2026. Overlapping qualifiers collapsed to one episode.
Output: tool/break_data_60/<code>_<Ddate>.csv  (60 rows, zero-padded symbol). All local.
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
OUT_DIR = os.path.join(TOOL_DIR, "break_data_60")

BASE = 30      # flat base (before-window)
UP = 30        # up-leg
R_MIN = 0.707  # Pearson r floor  (R^2 >= 0.5)
YEARS = {2025, 2026}


def main():
    ap = argparse.ArgumentParser(description="Extract 60-day flat-base -> +15% climb samples")
    ap.add_argument("--win", type=float, default=15.0, help="up-leg net %% bar (default 15)")
    ap.add_argument("--flat", type=float, default=4.0, help="base net within +/- this %% (default 4)")
    ap.add_argument("--r", type=float, default=R_MIN, help="min Pearson r of up-leg (default 0.707 = R^2 0.5)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))
    print(f"Scanning {len(files)} stocks: base={BASE}d flat(|net|<={args.flat:.0f}%) -> "
          f"up={UP}d (net>=+{args.win:.0f}%, r>={args.r:.3f}), years {sorted(YEARS)}...", file=sys.stderr)

    xs = np.arange(UP, dtype=float)
    xs_c = xs - xs.mean()
    xs_ss = np.sqrt((xs_c ** 2).sum())

    written = 0
    stocks_hit = 0
    period = BASE + UP
    for n, fn in enumerate(files, 1):
        if n % 200 == 0:
            print(f"  ... {n}/{len(files)} stocks, {written} samples", file=sys.stderr)
        code = fn[:-4]
        try:
            df = pd.read_csv(os.path.join(HISTORY_DIR, fn), parse_dates=["Date"])
        except Exception:
            continue
        if df.empty or "Close" not in df.columns or len(df) < period + 1:
            continue
        df = df.sort_values("Date").reset_index(drop=True)
        c = df["Close"].values
        o = df["Open"].values
        dts = df["Date"]
        N = len(df)

        qual = []
        for i in range(BASE - 1, N - UP):           # i = decision day D
            if dts.iloc[i].year not in YEARS:
                continue
            base0 = c[i - (BASE - 1)]
            if base0 <= 0:
                continue
            base_net = (c[i] - base0) / base0 * 100
            if not (-args.flat <= base_net <= args.flat):
                continue
            entry = o[i + 1]
            exit_ = c[i + UP]
            if entry <= 0:
                continue
            net = (exit_ - entry) / entry * 100
            if net < args.win:
                continue
            ys = c[i + 1: i + UP + 1]                # 30 up-leg closes
            ys_c = ys - ys.mean()
            denom = xs_ss * np.sqrt((ys_c ** 2).sum())
            if denom == 0:
                continue
            r = float((xs_c * ys_c).sum() / denom)   # Pearson r (slope sign = r sign)
            if r < args.r:
                continue
            qual.append(i)

        qual_set = set(qual)
        kept = [i for i in qual if (i - 1) not in qual_set]   # collapse consecutive runs
        if kept:
            stocks_hit += 1
        for i in kept:
            window = df.iloc[i - (BASE - 1): i + UP + 1].copy()   # 60 rows
            if len(window) != period:
                continue
            if "symbol" in window.columns:
                window["symbol"] = code
            ddate = dts.iloc[i].strftime("%Y%m%d")
            window.to_csv(os.path.join(OUT_DIR, f"{code}_{ddate}.csv"), index=False)
            written += 1

    print("\n" + "=" * 60)
    print(f"BREAK-60 SAMPLES  base={BASE}d flat(+/-{args.flat:.0f}%) -> up={UP}d (+{args.win:.0f}%, r>={args.r:.3f})")
    print("=" * 60)
    print(f"samples written .... {written:,}")
    print(f"stocks with >=1 .... {stocks_hit:,} / {len(files):,}")
    print(f"folder ............. {OUT_DIR}")
    print(f"each file .......... {period} rows (30 flat base + 30 up-leg), zero-padded symbol")


if __name__ == "__main__":
    main()
