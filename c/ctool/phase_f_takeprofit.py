#!/usr/bin/env python3
"""Phase F: Test the take-profit band (framed.md §4C, +8% to +15%).

Questions:
1. What % of breakouts reach +8%, +10%, +12%, +15%, +20% within 10 days?
2. Is 8-15% the optimal band, or are we capping winners too early / too late?
3. What's the max favorable excursion (MFE) distribution?

Method: Use break_data_40 (6,290 breakout events). For each, the filename
encodes the breakout detection date. Entry = next-day open. Compute forward
returns and MFE over the next 10 trading days.
"""

import os
import sys
from collections import defaultdict
from pathlib import Path

BREAK_DIR = Path(__file__).resolve().parent / "break_data_40"


def load_break_file(path: Path):
    """Load OHLCV data from a break_data file, return list of (date, o, h, l, c)."""
    rows = []
    with open(path) as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 6:
                continue
            date = parts[0]
            try:
                o, h, l, c = float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
            except ValueError:
                continue
            rows.append((date, o, h, l, c))
    return rows


def find_entry_idx(rows, break_date_str):
    """Given a breakout date, find the index of the NEXT trading day (entry)."""
    for i, (date, _, _, _, _) in enumerate(rows):
        if date > break_date_str:
            return i
    return None


def compute_forward(rows, entry_idx, max_days=10):
    """Compute forward returns and max favorable excursion from entry.

    Returns:
        reached: set of % thresholds reached (5, 8, 10, 12, 15, 20)
        mfe: max high / entry_open - 1 (in %)
        mae: min low / entry_open - 1 (in %)
        ret_close: {day: return%} for each holding day
    """
    entry_open = rows[entry_idx][1]
    if entry_open <= 0:
        return None

    reached = set()
    mfe = 0.0
    mae = 999.0
    ret_close = {}

    thresholds = [5, 8, 10, 12, 15, 20, 25, 30]

    end_idx = min(entry_idx + max_days, len(rows))
    for day_offset, idx in enumerate(range(entry_idx, end_idx)):
        day = day_offset + 1  # 1-based holding day
        _, o, h, l, c = rows[idx]

        # MFE: how far the HIGH went
        day_high_pct = (h / entry_open - 1) * 100
        mfe = max(mfe, day_high_pct)

        # MAE: how far the LOW went
        day_low_pct = (l / entry_open - 1) * 100
        mae = min(mae, day_low_pct)

        # Close-based return
        ret_close[day] = (c / entry_open - 1) * 100

        # Did we reach any threshold today?
        for t in thresholds:
            if h >= entry_open * (1 + t / 100):
                reached.add(t)

    return {
        "reached": reached,
        "mfe": mfe,
        "mae": mae,
        "ret_close": ret_close,
    }


def main():
    print("=" * 70)
    print(" Phase F: Take-Profit Band Calibration (+8% to +15%)")
    print("=" * 70)

    files = sorted(BREAK_DIR.glob("*.csv"))
    print(f"  Breakout event files: {len(files)}")

    # Parse filenames: SYMBOL_DATE.csv
    results = []
    skipped = 0

    for fp in files:
        stem = fp.stem  # e.g., "000008_20250328"
        parts = stem.split("_")
        if len(parts) < 2:
            skipped += 1
            continue
        break_date = parts[-1]  # YYYYMMDD
        break_date_str = f"{break_date[:4]}-{break_date[4:6]}-{break_date[6:8]}"

        rows = load_break_file(fp)
        if len(rows) < 15:
            skipped += 1
            continue

        entry_idx = find_entry_idx(rows, break_date_str)
        if entry_idx is None or entry_idx + 5 > len(rows):
            skipped += 1
            continue

        fwd = compute_forward(rows, entry_idx, max_days=10)
        if fwd is None:
            skipped += 1
            continue

        results.append(fwd)

    print(f"  Valid events analyzed: {len(results)}")
    print(f"  Skipped (data issues): {skipped}")
    print()

    # --- Analysis ---
    n = len(results)
    if n == 0:
        print("  ERROR: No valid events.")
        return

    # 1. Threshold reach rates
    print("  --- F1: Threshold reach rates within 10 days ---")
    print(f"  {'Threshold':>10s}  {'Reached':>8s}  {'Rate':>7s}  {'Cum. captured':>15s}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*7}  {'-'*15}")
    thresholds = [5, 8, 10, 12, 15, 20, 25, 30]
    for t in thresholds:
        count = sum(1 for r in results if t in r["reached"])
        pct = count / n * 100
        print(f"  {t:>+6}%     {count:>6d}     {pct:>5.1f}%  {'—':>15s}")

    print()

    # 2. MFE distribution
    mfes = sorted([r["mfe"] for r in results])
    maes = sorted([r["mae"] for r in results])

    def percentile(vals, p):
        idx = int(len(vals) * p / 100)
        return vals[min(idx, len(vals) - 1)]

    print("  --- F2: Max Favorable Excursion (MFE) distribution ---")
    for p in [10, 25, 50, 75, 90, 95]:
        print(f"    P{p:2d}: {percentile(mfes, p):+7.2f}%")
    print(f"    Mean MFE: {sum(mfes)/len(mfes):+.2f}%")
    print()

    print("  --- F3: Max Adverse Excursion (MAE) distribution ---")
    for p in [10, 25, 50, 75, 90, 95]:
        print(f"    P{p:2d}: {percentile(maes, p):+7.2f}%")
    print(f"    Mean MAE: {sum(maes)/len(maes):+.2f}%")
    print()

    # 3. Profit capture simulation: TP1 sell half at X%, TP2 sell rest at Y%
    # Simulate for various (tp1, tp2) pairs
    # Decision rules:
    #   - If high >= tp1, sell half at tp1 (capture tp1% on half)
    #   - If high >= tp2, sell rest at tp2 (capture tp2% on rest)
    #   - Otherwise, capture the final close return
    print("  --- F4: Simulated P&L for different (tp1, tp2) bands ---")
    print(f"  {'tp1':>6s}  {'tp2':>6s}  {'mean_ret':>9s}  {'win%':>6s}  {'avg_win':>8s}  {'avg_loss':>9s}")
    print(f"  {'-'*6}  {'-'*6}  {'-'*9}  {'-'*6}  {'-'*8}  {'-'*9}")

    tp_pairs = [
        (6, 12), (6, 15), (8, 12), (8, 15), (8, 18),
        (10, 15), (10, 18), (10, 20), (12, 18), (12, 20),
        (15, 20), (15, 25),
    ]
    # Baseline: no take-profit (just hold to day 10)
    tp_pairs.insert(0, (None, None))

    for tp1, tp2 in tp_pairs:
        simulated_rets = []
        for r in results:
            entry_open = 1.0  # normalized
            # Simulate: half at tp1, rest at tp2
            half_pct = 0.5
            rest_pct = 0.5

            ret = 0.0
            if tp1 is None:
                # No take-profit: just use day-10 close return
                ret = r["ret_close"].get(10, r["mfe"] * 0.5)  # fallback
            else:
                # tp1 on half if reached
                if tp1 in r["reached"]:
                    ret += half_pct * tp1
                else:
                    ret += half_pct * r["ret_close"].get(10, r["mfe"])
                # tp2 on rest if reached
                if tp2 in r["reached"]:
                    ret += rest_pct * tp2
                else:
                    ret += rest_pct * r["ret_close"].get(10, r["mfe"])

            simulated_rets.append(ret)

        mean_ret = sum(simulated_rets) / len(simulated_rets)
        wins = [r for r in simulated_rets if r > 0]
        losses = [r for r in simulated_rets if r <= 0]
        win_pct = len(wins) / len(simulated_rets) * 100
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        tp1_str = f"{tp1}%" if tp1 else "none"
        tp2_str = f"{tp2}%" if tp2 else "none"
        print(f"  {tp1_str:>6s}  {tp2_str:>6s}  {mean_ret:>+8.2f}%  {win_pct:>5.1f}%  {avg_win:>+7.2f}%  {avg_loss:>+8.2f}%")

    print()

    # 4. Day-by-day reach rates: when do breakouts hit each threshold?
    print("  --- F5: Cumulative reach rate by holding day ---")
    for t in [5, 8, 10, 12, 15, 20]:
        by_day = []
        for day in range(1, 11):
            count = sum(1 for r in results if any(
                r["ret_close"].get(d, -999) >= t or
                (d <= 10 and t in r["reached"])
                for d in range(1, day + 1)
            ))
            by_day.append(count / n * 100)
        day_str = "  ".join(f"D{d}:{by_day[d-1]:.0f}%" for d in [1, 2, 3, 5, 7, 10])
        print(f"    +{t:>2}%:  {day_str}")

    print()
    print("  --- F6: Key metrics ---")
    # What if we just hold all the way to day 10?
    d10_rets = [r["ret_close"].get(10, r["mfe"] * 0.5) for r in results]
    print(f"    Hold-to-day-10 mean: {sum(d10_rets)/len(d10_rets):+.2f}%")
    print(f"    Hold-to-day-10 win%: {sum(1 for r in d10_rets if r>0)/len(d10_rets)*100:.1f}%")

    # What % of breakouts never reach +5%?
    never_5 = sum(1 for r in results if 5 not in r["reached"])
    print(f"    Never reach +5%: {never_5}/{n} ({never_5/n*100:.1f}%)")

    # What % reach +8% but not +15%?
    reach8_not15 = sum(1 for r in results if 8 in r["reached"] and 15 not in r["reached"])
    print(f"    Reach +8% but not +15%: {reach8_not15}/{n} ({reach8_not15/n*100:.1f}%)")
    print(f"    → These are trades where TP1 fires but TP2 never does — capital tied up")

    print()
    print("=" * 70)
    print(" Phase F complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
