#!/usr/bin/env python3
"""
Gate backtest — replay framed.md §1A index gate over a historical range.

Reuses index.py's classify_index() / overall_light() UNCHANGED, so the verdict
for every past day is identical to what `index.py` would have printed live.
Note: the live [Verdict] is decided purely by §1A (the three index lights);
§1B sentiment is informational only and does NOT enter the verdict — so this
backtest, which uses only the cached index daily history, is fully faithful.

Data: reads the cached index_<code>.txt files that index.py already wrote
(~2-year lookback). NO network. If your range isn't covered, run `index.py`
once first (it caches ~2 years back from today).

Usage:
    python gate_backtest.py                 # default year 2025
    python gate_backtest.py --year 2024
    python gate_backtest.py --start 2025-01-01 --end 2025-06-30
    python gate_backtest.py --list          # also print every day's verdict
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force UTF-8 stdout/stderr (Windows cp1252 safety); no-op on Linux.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from index import INDEXES, classify_index, overall_light, read_existing, DEFAULT_OUTDIR


def load_series(outdir):
    series = {}
    for code, name, _ in INDEXES:
        path = os.path.join(outdir, f"index_{code}.txt")
        df, _last = read_existing(path)
        if df is None:
            print(f"  x missing/empty cache: {path}")
            print(f"    run `python index.py` once first to populate it.")
            sys.exit(1)
        df = df.copy()
        df["Date"] = df["Date"].astype(str)
        series[code] = df.reset_index(drop=True)
    return series


def replay(series, start, end):
    """For each trading date in [start, end] present in ALL indices, slice each
    index up to that day, classify it, then take the combined verdict."""
    base = series["000001"]
    dates = [d for d in base["Date"] if start <= d <= end]
    results = []                         # (date, verdict)
    for d in dates:
        per = {}
        ok = True
        for code, df in series.items():
            sub = df[df["Date"] <= d]
            if len(sub) < 12 or sub["Date"].iloc[-1] != d:   # need history + a real row on day d
                ok = False
                break
            per[code] = classify_index(sub)
        if not ok or not all(per.values()):
            continue
        results.append((d, overall_light(per)))
    return results


def longest_streak(results, light):
    best = cur = 0
    for _, v in results:
        cur = cur + 1 if v == light else 0
        best = max(best, cur)
    return best


def main():
    ap = argparse.ArgumentParser(description="Replay framed.md §1A gate over a date range (cached index data, no network)")
    ap.add_argument("--year", type=int, default=2025, help="calendar year to test (default 2025)")
    ap.add_argument("--start", default=None, help="start YYYY-MM-DD (overrides --year)")
    ap.add_argument("--end", default=None, help="end YYYY-MM-DD (overrides --year)")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--list", action="store_true", help="print every day's verdict")
    args = ap.parse_args()

    start = args.start or f"{args.year}-01-01"
    end = args.end or f"{args.year}-12-31"

    series = load_series(args.outdir)
    results = replay(series, start, end)

    if not results:
        print(f"No covered trading days in {start}..{end}. Cache may not reach back that far —")
        print("run `python index.py` once (it pulls ~2 years), or pick a more recent range.")
        sys.exit(1)

    n = len(results)
    g = sum(1 for _, v in results if v == "GREEN")
    a = sum(1 for _, v in results if v == "AMBER")
    r = sum(1 for _, v in results if v == "RED")
    span = f"{results[0][0]} .. {results[-1][0]}"

    if args.list:
        for d, v in results:
            print(f"  {d}  {v}")
        print("")

    print("=" * 60)
    print(f" Gate backtest (framed.md §1A)   {start} .. {end}")
    print(f" Covered trading days: {n}   ({span})")
    print("=" * 60)
    print(f"  GREEN : {g:>4}  ({g/n*100:.1f}%)   offensive — can build normally")
    print(f"  AMBER : {a:>4}  ({a/n*100:.1f}%)   light — strongest main-line only")
    print(f"  RED   : {r:>4}  ({r/n*100:.1f}%)   no new entries / cash")
    print("-" * 60)
    print(f"  Buyable, GREEN only      : {g}  ({g/n*100:.1f}%)")
    print(f"  Buyable, GREEN + AMBER   : {g+a}  ({(g+a)/n*100:.1f}%)")
    print(f"  Longest RED streak       : {longest_streak(results,'RED')} trading days")
    print(f"  Longest GREEN streak     : {longest_streak(results,'GREEN')} trading days")


if __name__ == "__main__":
    main()
