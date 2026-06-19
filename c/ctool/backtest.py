#!/usr/bin/env python3
"""
backtest.py — the scoreboard for pre_break.py.

Runs pre_break's selection over EVERY trading day in a range, grades each pick by its
forward return (the same logic as grade_6test.py), and prints ONE summary you can
compare across parameter changes. This is the tool that turns tuning from eyeballing
into measurement.

It reuses pre_break.scan_day (so gates + scoring are EXACTLY what runs live) and
grade_6test.grade_one (so forward returns match the 6test tracker). Change a knob in
pre_break.py, re-run this, compare the hit-rate / avg-return. All data is local.

A "win" = net return >= --win percent over a --hold trading-day window.

Usage:
    python backtest.py --range 202501 202506
    python backtest.py --range 20250101 20250630 --win 7 --hold 10
    python backtest.py --range 202501 202506 --csv picks.csv   # dump per-pick rows

Honest caveats:
  * Speed: it re-scans all ~948 stocks per trading day (no caching yet), so a 6-month
    range takes a few minutes. Correct, just not fast.
  * Overfitting: a good number on ONE range proves nothing. Tune on one period, then
    re-run on a DIFFERENT untouched period before believing any change.
  * Fills: entry uses next-day open; it does NOT yet exclude picks that gap to a sealed
    limit-up at the open (unbuyable). On most pre-breakout picks that's rare, but it
    can flatter the result. Use --csv to inspect if a run looks too good.
"""

import os
import sys
import argparse

_PREBREAK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prebreak")
if _PREBREAK_DIR not in sys.path:
    sys.path.insert(0, _PREBREAK_DIR)

import pandas as pd

import pre_break
from pre_break import load_meta, scan_day
from grade_6test import grade_one

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
SHARE_DIR = os.path.join(TOOL_DIR, "share_data")


def _parse_bound(s: str, is_end: bool) -> pd.Timestamp:
    """Parse YYYYMM or YYYYMMDD. For YYYYMM, start->1st, end->month end."""
    y, m = s[:4], s[4:6]
    if len(s) >= 8:
        return pd.Timestamp(f"{y}-{m}-{s[6:8]}")
    first = pd.Timestamp(f"{y}-{m}-01")
    return (first + pd.offsets.MonthEnd(0)) if is_end else first


def trading_days(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    """Trading-day calendar in range, read from a reference stock's CSV (local data)."""
    ref = os.path.join(HISTORY_DIR, "000001.csv")
    if not os.path.exists(ref):
        cands = [f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv")]
        if not cands:
            sys.exit(f"no CSVs in {HISTORY_DIR}")
        ref = os.path.join(HISTORY_DIR, sorted(cands)[0])
    d = pd.read_csv(ref, parse_dates=["Date"])["Date"].sort_values()
    d = d[(d >= start) & (d <= end)]
    return [x.strftime("%Y-%m-%d") for x in d]


def main():
    ap = argparse.ArgumentParser(description="Backtest scoreboard for pre_break.py")
    ap.add_argument("--range", nargs=2, metavar=("START", "END"), required=True,
                    help="date range, YYYYMM or YYYYMMDD")
    ap.add_argument("--win", type=float, default=7.0,
                    help="win threshold in %% (default 7.0)")
    ap.add_argument("--hold", type=int, default=10,
                    help="hold window in trading days (default 10)")
    ap.add_argument("--top", type=int, default=pre_break.TOP_N,
                    help=f"picks per day (default {pre_break.TOP_N}, from pre_break.TOP_N)")
    ap.add_argument("--csv", metavar="PATH", help="also dump per-pick rows to this CSV")
    args = ap.parse_args()

    pre_break.TOP_N = args.top  # let the backtest override picks-per-day without editing the source

    start = _parse_bound(args.range[0], is_end=False)
    end = _parse_bound(args.range[1], is_end=True)

    print("Loading metadata...", file=sys.stderr)
    meta = load_meta()
    days = trading_days(start, end)
    if not days:
        sys.exit(f"no trading days found in {start.date()} .. {end.date()}")
    print(f"Scanning {len(days)} trading days "
          f"({days[0]} .. {days[-1]}), top {args.top}/day, {len(meta)} stocks in cap band...",
          file=sys.stderr)

    rows = []  # one dict per pick
    for i, date_str in enumerate(days, 1):
        if i % 10 == 0 or i == len(days):
            print(f"  ... {i}/{len(days)} days  ({date_str})", file=sys.stderr)
        for c in scan_day(date_str, meta, verbose=False):
            ret, err = grade_one(c["code"], date_str, hold=args.hold)
            rows.append({
                "date": date_str, "code": c["code"], "name": c["name"],
                "score": c["score"], "ret": ret, "err": err,
            })

    graded = [r for r in rows if r["err"] is None]
    dropped = len(rows) - len(graded)
    rets = [r["ret"] for r in graded]

    lines = []
    lines.append(f"Backtest {start.date()} -> {end.date()}")
    lines.append(f"Win = net >= +{args.win:.1f}% over {args.hold} market days "
                 f"(entry next-day open, exit day-{args.hold} close)")
    lines.append("")
    lines.append(f"Dates scanned ....... {len(days)} trading days")
    lines.append(f"Picks generated ..... {len(rows)}  (top {args.top}/day)")
    lines.append(f"Picks graded ........ {len(graded)}  ({dropped} dropped: <{args.hold} forward days)")

    if graded:
        s = pd.Series(rets)
        hits = int((s >= args.win).sum())
        lines.append(f"Hits (>= +{args.win:.1f}%) .... {hits}")
        lines.append(f"Hit rate ............ {hits / len(graded) * 100:.1f}%")
        lines.append(f"Avg return .......... {s.mean():+.2f}%")
        lines.append(f"Median return ....... {s.median():+.2f}%")

        ranked = sorted(graded, key=lambda r: r["ret"], reverse=True)
        lines.append("")
        lines.append("Best 5:")
        for r in ranked[:5]:
            lines.append(f"  {r['ret']:+7.1f}%  {r['code']}  {r['name']}  ({r['date']})")
        lines.append("Worst 5:")
        for r in reversed(ranked[-5:]):
            lines.append(f"  {r['ret']:+7.1f}%  {r['code']}  {r['name']}  ({r['date']})")

        # does a higher score actually mean a better outcome? (the key tuning question)
        lines.append("")
        lines.append("By score bucket (does scoring add value?):")
        lines.append(f"  {'bucket':<10}{'n':>5}{'hit%':>8}{'avg%':>9}")
        buckets = [("score>=80", 80, 1e9), ("70-79", 70, 80),
                   ("60-69", 60, 70), ("45-59", 45, 60)]
        for label, lo, hi in buckets:
            grp = [r["ret"] for r in graded if lo <= r["score"] < hi]
            if grp:
                gs = pd.Series(grp)
                hr = (gs >= args.win).mean() * 100
                lines.append(f"  {label:<10}{len(grp):>5}{hr:>7.1f}%{gs.mean():>+8.1f}%")
    else:
        lines.append("No gradeable picks in this range.")

    report = "\n".join(lines)
    print("\n" + report)

    os.makedirs(SHARE_DIR, exist_ok=True)
    out_path = os.path.join(SHARE_DIR, f"backtest_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\nSaved summary -> {out_path}", file=sys.stderr)

    if args.csv:
        pd.DataFrame(rows).to_csv(args.csv, index=False)
        print(f"Saved per-pick rows -> {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
