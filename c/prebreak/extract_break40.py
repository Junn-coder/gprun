#!/usr/bin/env python3
"""
extract_break40.py — 40-day samples for the "more history, same target" test.

Window (40 days) = 30 feature days (NO constraint) + 10-day up-leg.  Decision day D = day 30.
Identical up-leg target to the 30-day model (the one that scored AUC 0.80), so the ONLY
thing that changes vs that model is 10 extra days of feature history.

    rows  1..30 : feature days (unconstrained) — what the picker sees at D
    rows 31..40 : the +15% loose up-leg (the target)

Up-leg qualifies (loose pure-up, same as the 30-day model):
    entry = open[D+1], exit = close[D+10], net = (exit-entry)/entry*100 >= +15
    AND no close in the leg sits more than 5% below entry

Decision day restricted to 2025 + 2026. Overlapping qualifiers collapsed to one episode.
Output: tool/break_data_40/<code>_<Ddate>.csv (40 rows, zero-padded symbol). All local.
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
CTOOL_DIR = os.path.join(os.path.dirname(TOOL_DIR), "ctool")
HISTORY_DIR = os.path.join(CTOOL_DIR, "stock_history_ak")
OUT_DIR = os.path.join(CTOOL_DIR, "break_data_40")

BEFORE = 30
HOLD = 10
YEARS = {2025, 2026}


def main():
    ap = argparse.ArgumentParser(description="Extract 40-day (30 feature + 10 up-leg) +15% samples")
    ap.add_argument("--win", type=float, default=15.0, help="up-leg net %% bar (default 15)")
    ap.add_argument("--dd", type=float, default=5.0, help="max %% a close may sit below entry (default 5)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    period = BEFORE + HOLD
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))
    print(f"Scanning {len(files)} stocks: {BEFORE} feature days + {HOLD}d up-leg "
          f"(net>=+{args.win:.0f}%, dd<={args.dd:.0f}%), years {sorted(YEARS)}...", file=sys.stderr)

    written = 0
    stocks_hit = 0
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
        o, c = df["Open"], df["Close"]
        dts = df["Date"]

        entry = o.shift(-1)
        exit_ = c.shift(-HOLD)
        fut_min = pd.concat([c.shift(-i) for i in range(1, HOLD + 1)], axis=1).min(axis=1)
        net = (exit_ - entry) / entry * 100
        dd_ok = fut_min >= entry * (1 - args.dd / 100.0)
        yr = dts.dt.year
        qual = (net >= args.win) & dd_ok & entry.notna() & exit_.notna() & yr.isin(YEARS)

        pos = set(i for i in np.flatnonzero(qual.values) if i >= BEFORE - 1 and i + HOLD < len(df))
        kept = [i for i in sorted(pos) if (i - 1) not in pos]
        if kept:
            stocks_hit += 1
        for i in kept:
            window = df.iloc[i - (BEFORE - 1): i + HOLD + 1].copy()   # 40 rows
            if len(window) != period:
                continue
            if "symbol" in window.columns:
                window["symbol"] = code
            ddate = dts.iloc[i].strftime("%Y%m%d")
            window.to_csv(os.path.join(OUT_DIR, f"{code}_{ddate}.csv"), index=False)
            written += 1

    print("\n" + "=" * 56)
    print(f"BREAK-40 SAMPLES  {BEFORE}+{HOLD}d  net>=+{args.win:.0f}% (dd<={args.dd:.0f}%)  {sorted(YEARS)}")
    print("=" * 56)
    print(f"samples written .... {written:,}")
    print(f"stocks with >=1 .... {stocks_hit:,} / {len(files):,}")
    print(f"folder ............. {OUT_DIR}")
    print(f"each file .......... {period} rows (30 feature + 10 up-leg), zero-padded symbol")


if __name__ == "__main__":
    main()
