#!/usr/bin/env python3
"""
Phase B: Calibrate the ATR stop multiplier.
Targets framed.md §4A (ATR multiplier) and §8 (board-type differentiation).

Method:
- Load all stocks from stock_history_ak/
- Compute 10-day ATR as % of closing price
- Compute single-day absolute move (|close - prev_close| / close) as %
- For each stock/day, compute: |daily_move_pct| / ATR_pct
- Stratify by board type (main, ChiNext 300xxx, STAR 688xxx)
- Report: what % of daily moves fall within N× ATR?
- Answer: what multiplier keeps the stop wider than ~80% of single-day noise?
"""

import csv
import os
from collections import defaultdict
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "stock_history_ak")

def classify_board(code: str) -> str:
    """Classify stock code into board type."""
    code = code.strip()
    if code.startswith("300"):
        return "chiNext"
    elif code.startswith("688"):
        return "star"
    elif code[0] in "06":
        return "main"
    else:
        return "other"


def load_stock(path: str) -> list[dict]:
    """Load a stock_history_ak CSV. Returns list of dicts sorted by date."""
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    "date": r["Date"],
                    "open": float(r["Open"]),
                    "high": float(r["High"]),
                    "low": float(r["Low"]),
                    "close": float(r["Close"]),
                    "volume": float(r["Volume"]),
                })
            except (ValueError, KeyError):
                continue
    rows.sort(key=lambda x: x["date"])
    return rows


def compute_ratios(rows: list[dict]) -> list[float]:
    """
    For each day (after the first 11), compute:
      10-day ATR as % of close
      |daily_change_pct| / ATR_pct
    Returns list of ratios.
    """
    if len(rows) < 12:
        return []

    closes = np.array([r["close"] for r in rows])
    highs = np.array([r["high"] for r in rows])
    lows = np.array([r["low"] for r in rows])

    # True Range
    tr = np.maximum(
        highs - lows,
        np.maximum(
            np.abs(highs - np.roll(closes, 1)),
            np.abs(lows - np.roll(closes, 1)),
        ),
    )
    tr[0] = highs[0] - lows[0]  # first day: use H-L

    # 10-day ATR (simple moving average of TR)
    atr = np.full(len(tr), np.nan)
    for i in range(10, len(tr)):
        atr[i] = np.mean(tr[i - 9 : i + 1])

    # ATR as % of close
    atr_pct = atr / closes * 100.0

    # Daily absolute change as % (skip first row, need prev close)
    daily_move_pct = np.abs(closes[1:] / closes[:-1] - 1.0) * 100.0

    # Ratio: daily_move_pct / atr_pct (only where both valid)
    ratios = []
    for i in range(1, len(rows)):
        if not np.isnan(atr_pct[i]) and atr_pct[i] > 0:
            ratios.append(daily_move_pct[i - 1] / atr_pct[i])

    return ratios


def main():
    files = sorted(os.listdir(DATA_DIR))
    all_ratios = defaultdict(list)
    stock_counts = defaultdict(int)

    for fname in files:
        if not fname.endswith(".csv"):
            continue
        code = fname.replace(".csv", "")
        board = classify_board(code)
        if board == "other":
            continue

        path = os.path.join(DATA_DIR, fname)
        rows = load_stock(path)
        ratios = compute_ratios(rows)
        if ratios:
            all_ratios[board].extend(ratios)
            stock_counts[board] += 1

    # Report
    print("=" * 70)
    print("Phase B: ATR Stop Multiplier Calibration")
    print("=" * 70)
    print(f"\nStocks loaded: {dict(stock_counts)}")
    print(f"Observations per board:")
    for b in ["main", "chiNext", "star"]:
        print(f"  {b:>10}: {len(all_ratios[b]):,} days (after warmup)")

    # Percentiles of the ratio distribution
    percentiles = [50, 60, 70, 75, 80, 85, 90, 95, 99]
    print(f"\n{'Board':>10} | {'Obs':>10} |", end="")
    for p in percentiles:
        print(f" P{p:>2} |", end="")
    print()
    print("-" * (22 + 9 * len(percentiles)))

    for b in ["main", "chiNext", "star"]:
        arr = np.array(all_ratios[b])
        print(f"{b:>10} | {len(arr):>10,} |", end="")
        for p in percentiles:
            print(f" {np.percentile(arr, p):.2f} |", end="")
        print()

    # Coverage: what % of daily moves fall within N× ATR?
    print(f"\n{'=' * 70}")
    print("Coverage: % of daily moves ≤ N× ATR")
    print(f"{'=' * 70}")
    multipliers = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
    print(f"\n{'Multiplier':>12} | {'main %':>8} | {'chiNext %':>10} | {'star %':>8}")
    print("-" * 52)
    for m in multipliers:
        cov_main = (np.array(all_ratios["main"]) <= m).mean() * 100
        cov_cn = (np.array(all_ratios["chiNext"]) <= m).mean() * 100
        cov_star = (np.array(all_ratios["star"]) <= m).mean() * 100
        print(f"  {m:>8.2f}×   | {cov_main:>7.1f}%  | {cov_cn:>9.1f}%  | {cov_star:>7.1f}%")

    # Descriptive stats
    print(f"\n{'=' * 70}")
    print("Descriptive stats (ratio = |daily_move_pct| / ATR_pct)")
    print(f"{'=' * 70}")
    for b in ["main", "chiNext", "star"]:
        arr = np.array(all_ratios[b])
        print(f"\n{b}:")
        print(f"  Mean: {arr.mean():.3f}  Median: {np.median(arr):.3f}  Std: {arr.std():.3f}")
        print(f"  Min: {arr.min():.3f}  Max: {arr.max():.3f}")
        print(f"  % > 1.0× ATR: {(arr > 1.0).mean() * 100:.1f}%")
        print(f"  % > 1.5× ATR: {(arr > 1.5).mean() * 100:.1f}%")
        print(f"  % > 2.0× ATR: {(arr > 2.0).mean() * 100:.1f}%")

    # Recommendation
    print(f"\n{'=' * 70}")
    print("RECOMMENDATION (stop = N× ATR below entry)")
    print(f"{'=' * 70}")
    print(f"\nInterpretation: at N× ATR, (100 - coverage)% of daily noise breaches the stop.")
    print(f"A wider stop = fewer noise triggers, but more capital at risk per trade.")

    for b in ["main", "chiNext", "star"]:
        arr = np.array(all_ratios[b])
        p80 = np.percentile(arr, 80)
        p85 = np.percentile(arr, 85)
        p90 = np.percentile(arr, 90)
        current = {"main": 1.0, "chiNext": 1.0, "star": 1.0}.get(b, 1.0)  # Phase B: all unified 1.0×
        cov = (arr <= current).mean() * 100

        print(f"\n{b}:")
        print(f"  Noise percentiles: 80th={p80:.2f}×  85th={p85:.2f}×  90th={p90:.2f}×")
        print(f"  Current rule: {current}× ATR  → covers {cov:.1f}% of daily noise")
        print(f"  → {100-cov:.1f}% of days breach this stop by noise alone")

        # What if we used 1.0× across all boards?
        cov1 = (arr <= 1.0).mean() * 100
        print(f"  If 1.0× ATR were used: covers {cov1:.1f}% → {100-cov1:.1f}% breach rate")


    # ATR distribution (absolute %) — needed for floor/cap decisions
    print(f"\n{'=' * 70}")
    print("ATR (as % of close) distribution — for floor/cap decisions")
    print(f"{'=' * 70}")
    print(f"\nCurrent rule: max(5%, N× ATR), cap 10%")

    atr_pcts_by_board = defaultdict(list)
    for fname in files:
        if not fname.endswith(".csv"):
            continue
        code = fname.replace(".csv", "")
        board = classify_board(code)
        if board == "other":
            continue
        path = os.path.join(DATA_DIR, fname)
        rows = load_stock(path)
        if len(rows) < 12:
            continue
        closes = np.array([r["close"] for r in rows])
        highs = np.array([r["high"] for r in rows])
        lows = np.array([r["low"] for r in rows])
        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
        tr[0] = highs[0] - lows[0]
        atr = np.full(len(tr), np.nan)
        for i in range(10, len(tr)):
            atr[i] = np.mean(tr[i - 9 : i + 1])
        atr_pct = atr / closes * 100.0
        valid = atr_pct[~np.isnan(atr_pct)]
        atr_pcts_by_board[board].extend(valid.tolist())

    for b in ["main", "chiNext", "star"]:
        arr = np.array(atr_pcts_by_board[b])
        pcts = [10, 25, 50, 75, 90, 95]
        print(f"\n{b} (n={len(arr):,}):")
        print(f"  Mean ATR%: {arr.mean():.2f}%")
        for p in pcts:
            print(f"  P{p}: {np.percentile(arr, p):.2f}%")
        # How often is 10-day ATR < 5%? (floor analysis)
        below_5 = (arr < 5.0).mean() * 100
        print(f"  % of days ATR < 5% (floor triggers): {below_5:.1f}%")
        above_10 = (arr > 10.0).mean() * 100
        print(f"  % of days ATR > 10% (cap triggers): {above_10:.1f}%")

    # Combined: actual stop width for each board
    print(f"\n{'=' * 70}")
    print("Current stop width (all boards: max(5%, 1.0×ATR) cap 10%, Phase B calibrated)")
    print("                  (old rule was main 1.5×, ChiNext/STAR 1.0×)")
    print(f"{'=' * 70}")
    for b, mult in [("main", 1.0), ("chiNext", 1.0), ("star", 1.0)]:
        arr = np.array(atr_pcts_by_board[b])
        # Apply the rule: max(5%, mult × ATR), cap 10%
        stop_widths = np.clip(np.maximum(5.0, mult * arr), 0, 10.0)
        pcts = [10, 25, 50, 75, 90, 95]
        print(f"\n{b} ({mult}× ATR):")
        print(f"  Mean stop width: {stop_widths.mean():.2f}%")
        for p in pcts:
            print(f"  P{p}: {np.percentile(stop_widths, p):.2f}%")


if __name__ == "__main__":
    main()
