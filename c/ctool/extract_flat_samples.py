#!/usr/bin/env python3
"""
extract_flat_samples.py — pick "flat / no-profit-chance" NEGATIVE samples (the contrast
class for the pure-up-turn trainer) and write one 30-day OHLCV file per sample into
tool/flat_data/.

Same 30-day window as the positives (20 before + 10 forward), but the forward 10-day leg
goes NOWHERE — sideways, no win, no crash:
    net = (close[D+10] - open[D+1]) / open[D+1] * 100   with  -FLAT <= net <= +FLAT

Windows are thinned to NON-OVERLAPPING per stock (step = period) so samples are distinct,
pooled across all years (2024 + 2025), shuffled with a fixed seed, and the first N kept.

Output: tool/flat_data/<code>_<Ddate>.csv  (Ddate = decision day, YYYYMMDD), zero-padded
symbol column. All data local.

Usage:
    python extract_flat_samples.py                  # 5000 flats, +/-5% band
    python extract_flat_samples.py --n 5000 --flat 5
"""
import os
import sys
import random
import argparse
from collections import defaultdict

import numpy as np
import pandas as pd

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
OUT_DIR = os.path.join(TOOL_DIR, "flat_data")

HOLD = 10
SEED = 42


def main():
    ap = argparse.ArgumentParser(description="Extract flat (no-profit-chance) negative samples")
    ap.add_argument("--period", type=int, default=30, help="window length; before = period-10 (default 30)")
    ap.add_argument("--flat", type=float, default=5.0, help="forward net must be within +/- this %% (default 5)")
    ap.add_argument("--n", type=int, default=5000, help="how many samples to write (default 5000)")
    args = ap.parse_args()

    before = args.period - HOLD
    if before < 5:
        sys.exit(f"period {args.period} too short (before window = {before})")

    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))
    print(f"Scanning {len(files)} stocks for flat windows (|net|<={args.flat:.0f}% over {HOLD}d)...",
          file=sys.stderr)

    # pass 1: collect distinct (non-overlapping) flat candidates
    candidates = []  # (code, i, ddate_str)
    for n, fn in enumerate(files, 1):
        if n % 200 == 0:
            print(f"  ... {n}/{len(files)} stocks, {len(candidates)} candidates", file=sys.stderr)
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
        net = (exit_ - entry) / entry * 100
        flat = (net.abs() <= args.flat) & entry.notna() & exit_.notna()

        last_kept = -10**9
        for i in np.flatnonzero(flat.values):
            if i < before - 1 or i + HOLD >= len(df):
                continue
            if i - last_kept < args.period:        # non-overlapping
                continue
            last_kept = i
            candidates.append((code, int(i), df.iloc[i]["Date"].strftime("%Y%m%d")))

    if not candidates:
        sys.exit("no flat candidates found")

    random.seed(SEED)
    random.shuffle(candidates)
    chosen = candidates[: args.n]

    # pass 2: group by code, re-read once, write windows
    by_code = defaultdict(list)
    for code, i, ddate in chosen:
        by_code[code].append((i, ddate))

    written = 0
    year_count = defaultdict(int)
    for code, items in by_code.items():
        df = pd.read_csv(os.path.join(HISTORY_DIR, f"{code}.csv"), parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        for i, ddate in items:
            window = df.iloc[i - (before - 1): i + HOLD + 1].copy()
            if len(window) != args.period:
                continue
            if "symbol" in window.columns:
                window["symbol"] = code
            window.to_csv(os.path.join(OUT_DIR, f"{code}_{ddate}.csv"), index=False)
            written += 1
            year_count[ddate[:4]] += 1

    print("\n" + "=" * 56)
    print(f"FLAT SAMPLES  period={args.period}  |net|<={args.flat:.0f}% over {HOLD}d")
    print("=" * 56)
    print(f"distinct candidates found .. {len(candidates):,}")
    print(f"samples written ............ {written:,}")
    print(f"by decision year ........... " + ", ".join(f"{y}:{year_count[y]}" for y in sorted(year_count)))
    print(f"folder ..................... {OUT_DIR}")
    print(f"each file .................. {args.period} rows (Date,symbol,Open,High,Low,Close,Volume)")


if __name__ == "__main__":
    main()
