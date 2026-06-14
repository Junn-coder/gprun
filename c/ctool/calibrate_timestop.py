#!/usr/bin/env python3
"""
Phase C: Calibrate the time stop.
Targets framed.md §4B (day-count and gain threshold).

Method:
- Load all breakout event files from break_data/ (3,170 events, 707 stocks)
- For each event: entry at signal-day close, track forward up to 10 days
- Record: first day reaching +1%, +2%, +3%, +5%, +8%
- Build cumulative distributions: "by day N, X% of entries reach +G%"
- Answer: is day-3 breakout time stop too tight? Is +2% the right threshold?

Also run on break_data_40/ (6,290 events, 820 stocks) for comparison.
"""

import csv
import os
import re
from collections import defaultdict
import numpy as np

DATA_DIRS = {
    "break_data": os.path.join(os.path.dirname(__file__), "break_data"),
    "break_data_40": os.path.join(os.path.dirname(__file__), "break_data_40"),
}


def load_event(path: str) -> list[dict]:
    """Load a breakout event CSV. Returns sorted rows."""
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    "date": r["Date"],
                    "close": float(r["Close"]),
                })
            except (ValueError, KeyError):
                continue
    rows.sort(key=lambda x: x["date"])
    return rows


def simulated_days_to_gain(rows: list[dict], signal_date: str, gains: list[float], max_days: int = 10) -> dict:
    """
    Given sorted rows and a signal date, simulate entry at signal day close.
    Returns dict: {gain_pct: days_to_first_reach or None}
    """
    # Find signal row index
    sig_idx = None
    for i, r in enumerate(rows):
        if r["date"] == signal_date:
            sig_idx = i
            break
    if sig_idx is None:
        return {}

    entry_price = rows[sig_idx]["close"]
    if entry_price <= 0:
        return {}

    results = {}
    for g in gains:
        target = entry_price * (1.0 + g / 100.0)
        days = None
        for offset in range(1, min(max_days, len(rows) - sig_idx)):
            future_close = rows[sig_idx + offset]["close"]
            if future_close >= target:
                days = offset
                break
        results[g] = days  # None = never reached within max_days

    return results


def process_dir(data_dir: str) -> dict:
    """Process all CSV files in a directory. Returns {gain_pct: [list of days or None]}"""
    gains = [1.0, 2.0, 3.0, 5.0, 8.0]
    results = {g: [] for g in gains}
    skipped = 0
    processed = 0

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".csv"):
            continue
        # Parse code_date from filename
        m = re.match(r"(\d+)_(\d{8})\.csv", fname)
        if not m:
            continue
        code, signal_date = m.groups()
        # Reformat date to YYYY-MM-DD
        signal_date = f"{signal_date[:4]}-{signal_date[4:6]}-{signal_date[6:]}"

        path = os.path.join(data_dir, fname)
        rows = load_event(path)
        if len(rows) < 2:
            skipped += 1
            continue

        days = simulated_days_to_gain(rows, signal_date, gains)
        if not days:
            skipped += 1
            continue

        for g in gains:
            results[g].append(days[g])
        processed += 1

    return results, processed, skipped


def percentile_curve(days_list: list, max_days: int = 10) -> dict:
    """
    Given a list of days (integers or None), compute cumulative:
    "by day N, what % of entries have reached the target?"
    """
    total = len(days_list)
    reached = sum(1 for d in days_list if d is not None)
    curve = {}
    for day in range(1, max_days + 1):
        cum = sum(1 for d in days_list if d is not None and d <= day)
        curve[day] = cum / total * 100.0
    curve["never"] = (total - reached) / total * 100.0
    curve["ever"] = reached / total * 100.0
    return curve


def main():
    for label, data_dir in DATA_DIRS.items():
        print(f"\n{'=' * 70}")
        print(f"Phase C: Time Stop Calibration — {label}")
        print(f"{'=' * 70}")

        results, n, skipped = process_dir(data_dir)
        print(f"\nProcessed: {n} events, skipped: {skipped} (no signal row or empty)")

        # Build curves for each gain threshold
        gains = [1.0, 2.0, 3.0, 5.0, 8.0]
        max_days = 10

        print(f"\n{'Gain threshold':>15} | {'Ever%':>6} |", end="")
        for d in range(1, max_days + 1):
            print(f" D{d:>2}% |", end="")
        print(f" {'Never%':>6}")
        print("-" * (25 + 10 * (max_days + 2)))

        for g in gains:
            curve = percentile_curve(results[g], max_days)
            print(f"       +{g:.0f}% entry gain | {curve['ever']:>5.1f}% |", end="")
            for d in range(1, max_days + 1):
                print(f" {curve[d]:>4.1f} |", end="")
            print(f" {curve['never']:>5.1f}%")

        # Key decision table
        print(f"\n{'=' * 70}")
        print(f"KEY METRICS ({label})")
        print(f"{'=' * 70}")
        for g in gains:
            days = [d for d in results[g] if d is not None]
            ever = len(days) / len(results[g]) * 100
            median_days = np.median(days) if days else float('nan')
            print(f"  +{g:.0f}%: ever={ever:.1f}%  median days={median_days:.1f}  n_reached={len(days)}/{len(results[g])}")

        # Decision: day-3 breakout time stop
        print(f"\n--- Current framed.md rules vs. data ---")
        print(f"  Breakout entries: day-3 gain < +2% => exit")
        for g in [1.0, 2.0, 3.0]:
            curve = percentile_curve(results[g], max_days)
            print(f"    +{g:.0f}% reached by day 3: {curve[3]:.1f}%")
        print(f"    → At +2% by day 3: the rule exits everything below this threshold")

        # Overall: by day N, what % reach +2%?
        print(f"\n--- Cumulative +2% reach rate by day ---")
        curve2 = percentile_curve(results[2.0], max_days)
        for d in range(1, max_days + 1):
            print(f"    Day {d:>2}: {curve2[d]:.1f}%")
        print(f"    Never: {curve2['never']:.1f}%")

    print()


if __name__ == "__main__":
    main()
