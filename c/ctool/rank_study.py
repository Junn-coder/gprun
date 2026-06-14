#!/usr/bin/env python3
"""
rank_study.py — STAGE A: does a BETTER way to rank the 2 daily picks exist?

The framed.md system only takes 2 trades per GREEN day, so WHICH 2 you pick is
everything. Today the picker ranks by sector-heat then turnover. This script asks,
on real history: if I had ranked the eligible candidates by some other feature and
taken the top 2, would those 2 have done better (run through the SAME framed.md §4
exit engine)?

For every GREEN day it gathers ALL eligible 首板/cap-OK candidates (not just 2),
computes cheap features at day T, simulates each as a real trade (entry T+1 open,
framed.md §4 exits), then scores each ranking rule by the mean realized net% of the
top-2 it would have chosen.

This does NOT apply the 2-slot concurrency cap — it isolates the RANKING question.
A rule that wins here is then wired into pick_candidates and re-checked with the real
¥50k/2-slot strategy_backtest on TRAIN + the untouched VALIDATION half (Stage B).

Usage:
    python rank_study.py                                  # TRAIN default
    python rank_study.py --start 2025-07-01 --end 2026-05-29   # VALIDATION
"""
import os
import sys
import argparse
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd

from gate_backtest import load_series, replay
from index import DEFAULT_OUTDIR
from strategy_backtest import (
    load_history, annotate, load_meta, simulate, board_limit,
    CAP_LOW, CAP_HIGH, TARGET_CNY, LOT, SEAL_TOL,
)


def main():
    ap = argparse.ArgumentParser(description="Stage-A ranking diagnostic")
    ap.add_argument("--start", default="2024-05-28")
    ap.add_argument("--end", default="2025-06-30")
    args = ap.parse_args()

    gseries = load_series(DEFAULT_OUTDIR)
    green = [d for d, v in replay(gseries, args.start, args.end) if v == "GREEN"]
    print(f"GREEN days in range: {len(green)}")

    hist = annotate(load_history())
    meta = load_meta()
    by_code = {c: g.reset_index(drop=True) for c, g in hist.groupby("symbol", sort=False)}
    all_dates = sorted(hist["Date"].unique())

    def next_date(d):
        i = all_dates.index(d) if d in all_dates else None
        return all_dates[i + 1] if (i is not None and i + 1 < len(all_dates)) else None

    # ---- gather every eligible candidate per GREEN day, with features + real outcome ----
    by_day = defaultdict(list)          # T -> list of candidate dicts
    elig_counts = []
    for T in green:
        day = hist[hist["Date"] == T]
        s = day[day["sealed"]].copy()
        if s.empty:
            continue
        s["industry"] = s["symbol"].map(meta["industry"])
        s["name"] = s["symbol"].map(meta["name"])
        s["cap"] = s["symbol"].map(meta["float_mcap_now"])
        s = s[s["industry"].notna()]
        s = s[~s["name"].astype(str).str.contains("ST|退", na=False)]
        if s.empty:
            continue
        heat = s.groupby("industry")["symbol"].count().to_dict()   # sealed count per sector
        elig = s[(s["board_count"] == 1) & (s["cap"].between(CAP_LOW, CAP_HIGH))]
        T1 = next_date(T)
        n_elig = 0
        for _, r in elig.iterrows():
            code = r["symbol"]
            g = by_code[code]
            i_list = g.index[g["Date"] == T].tolist()
            if not i_list:
                continue
            i = i_list[0]
            if i < 20:
                continue
            C = g.at[i, "Close"]
            mom5 = C / g.at[i - 5, "Close"] - 1.0
            mom20 = C / g.at[i - 20, "Close"] - 1.0
            hi20 = g.loc[i - 19:i, "High"].max()
            lo = max(0, i - 249)
            hi250 = g.loc[lo:i, "High"].max()
            amount = g.at[i, "Volume"] * C
            cap = r["cap"]
            # outcome: trade it for real (entry T+1 open, framed.md §4 exits)
            er = g.index[g["Date"] == T1].tolist() if T1 else []
            if not er:
                continue
            epos = er[0]
            erow = g.loc[epos]
            lim = board_limit(code)
            if erow["Open"] / C - 1.0 >= lim - SEAL_TOL:        # T+1 gap-seal: unfillable
                continue
            entry = erow["Open"]
            shares = int((TARGET_CNY / entry) // LOT * LOT)
            if shares < LOT:
                continue
            fills, _ = simulate(g.to_dict("records"), epos, entry, shares)
            cost = entry * shares
            proceeds = sum(sh * px for sh, px, _ in fills)
            net_pct = (proceeds - cost) / cost * 100.0
            n_elig += 1
            by_day[T].append(dict(
                code=code, name=r["name"], industry=r["industry"],
                zt_heat=heat.get(r["industry"], 1), amount=amount,
                turnover=amount / cap if cap else 0.0,
                mom5=mom5 * 100, mom20=mom20 * 100,
                dist20h=(C / hi20 - 1.0) * 100, dist250h=(C / hi250 - 1.0) * 100,
                cap_b=cap / 1e8, net_pct=net_pct,
            ))
        if n_elig:
            elig_counts.append(n_elig)

    days_with_data = len(by_day)
    all_cands = [c for v in by_day.values() for c in v]
    if not all_cands:
        sys.exit("no candidates collected")
    choice_days = sum(1 for v in by_day.values() if len(v) > 2)
    print(f"tradeable candidate-days: {days_with_data}   total candidates: {len(all_cands)}")
    print(f"avg eligible/day: {np.mean(elig_counts):.1f}   "
          f"days with >2 (ranking can help): {choice_days} ({choice_days/days_with_data*100:.0f}%)")
    print("=" * 70)

    # ---- evaluate ranking rules: top-2 per day by each key ----
    def current_pick(cands):
        ordered = sorted(cands, key=lambda c: (c["zt_heat"], c["amount"]), reverse=True)
        sel, seen = [], set()
        for c in ordered:
            if c["industry"] in seen:
                continue
            sel.append(c); seen.add(c["industry"])
            if len(sel) >= 2:
                break
        return sel

    def topk(cands, key, reverse):
        return sorted(cands, key=lambda c: c[key], reverse=reverse)[:2]

    rules = [
        ("CURRENT (heat+amount)", current_pick),
        ("amount  desc", lambda c: topk(c, "amount", True)),
        ("amount  asc",  lambda c: topk(c, "amount", False)),
        ("turnover desc", lambda c: topk(c, "turnover", True)),
        ("mom20  desc", lambda c: topk(c, "mom20", True)),
        ("mom20  asc",  lambda c: topk(c, "mom20", False)),
        ("mom5   desc", lambda c: topk(c, "mom5", True)),
        ("dist250h desc (near 52wk hi)", lambda c: topk(c, "dist250h", True)),
        ("dist20h  asc (far below 20d hi)", lambda c: topk(c, "dist20h", False)),
        ("cap_b   asc (smaller)", lambda c: topk(c, "cap_b", False)),
        ("cap_b   desc (larger)", lambda c: topk(c, "cap_b", True)),
    ]

    results = []
    for name, fn in rules:
        nets = []
        for cands in by_day.values():
            nets.extend(c["net_pct"] for c in fn(cands))
        nets = np.array(nets)
        results.append((name, nets.mean(), np.median(nets),
                        (nets > 0).mean() * 100, len(nets)))
    # pure baseline: take ALL eligible
    allnet = np.array([c["net_pct"] for c in all_cands])
    results.append(("(take-all baseline)", allnet.mean(), np.median(allnet),
                    (allnet > 0).mean() * 100, len(allnet)))

    results.sort(key=lambda r: r[1], reverse=True)
    print(f"{'ranking rule':<32}{'mean%':>8}{'med%':>8}{'win%':>7}{'n':>6}")
    print("-" * 70)
    for name, mean, med, win, n in results:
        mark = "  <= current" if name.startswith("CURRENT") else ""
        print(f"{name:<32}{mean:>8.2f}{med:>8.2f}{win:>7.0f}{n:>6}{mark}")


if __name__ == "__main__":
    main()
