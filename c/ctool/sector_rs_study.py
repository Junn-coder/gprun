#!/usr/bin/env python3
"""
sector_rs_study.py — STAGE A for OFF-CHART signal: does SECTOR relative-strength help?

The chart-only ranking features were all noise (rank_study.py). cqa.md's one hint was that
a stock in a HOT sector breaks out ~1.4x more often. Sector RS is the only off-chart signal
that is FREE and fully backfillable — it's computed from the 948 stocks + industry tags you
already have, no external data.

For every GREEN day it attaches each eligible candidate's SECTOR momentum (mean member-stock
5d/20d return) and the sector's RS percentile that day, then asks two questions:

  (1) RANKING : do the top-2 by sector RS beat current / random?  (the 2-pick question)
  (2) FILTER  : does restricting the POOL to hot sectors raise the average outcome?
                (the breadth-compatible question — raise pool quality, then take breadth)

Every candidate is run through the SAME framed.md §4 exit engine for its real outcome.
A signal only counts if it holds on TRAIN *and* the untouched VALIDATION half.

Usage:
    python sector_rs_study.py                                    # TRAIN
    python sector_rs_study.py --start 2025-07-01 --end 2026-05-29  # VALIDATION
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


def build_sector_table(hist, meta):
    """Per (Date, industry): mean member 5d/20d return + the sector's RS percentile that day."""
    h = hist[["Date", "symbol", "Close"]].copy()
    h["industry"] = h["symbol"].map(meta["industry"])
    h = h[h["industry"].notna()].sort_values(["symbol", "Date"])
    c5 = h.groupby("symbol")["Close"].shift(5)
    c20 = h.groupby("symbol")["Close"].shift(20)
    h["smom5"] = h["Close"] / c5 - 1.0
    h["smom20"] = h["Close"] / c20 - 1.0
    sec = (h.dropna(subset=["smom20"])
             .groupby(["Date", "industry"])
             .agg(sec_mom5=("smom5", "mean"), sec_mom20=("smom20", "mean"),
                  n=("symbol", "count")).reset_index())
    # RS percentile within the day by 20d sector momentum: 1.0 = hottest sector, 0.0 = coldest
    sec["pct"] = sec.groupby("Date")["sec_mom20"].rank(pct=True)
    return {(r.Date, r.industry): (r.sec_mom5, r.sec_mom20, r.pct)
            for r in sec.itertuples(index=False)}


def main():
    ap = argparse.ArgumentParser(description="Stage-A sector-RS diagnostic")
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
    sec_map = build_sector_table(hist, meta)

    def next_date(d):
        i = all_dates.index(d) if d in all_dates else None
        return all_dates[i + 1] if (i is not None and i + 1 < len(all_dates)) else None

    by_day = defaultdict(list)
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
        heat = s.groupby("industry")["symbol"].count().to_dict()
        elig = s[(s["board_count"] == 1) & (s["cap"].between(CAP_LOW, CAP_HIGH))]
        T1 = next_date(T)
        for _, r in elig.iterrows():
            code = r["symbol"]
            g = by_code[code]
            i_list = g.index[g["Date"] == T].tolist()
            if not i_list:
                continue
            i = i_list[0]
            C = g.at[i, "Close"]
            amount = g.at[i, "Volume"] * C
            sm = sec_map.get((T, r["industry"]))
            if sm is None:
                continue
            sec_mom5, sec_mom20, sec_pct = sm
            er = g.index[g["Date"] == T1].tolist() if T1 else []
            if not er:
                continue
            epos = er[0]
            erow = g.loc[epos]
            lim = board_limit(code)
            if erow["Open"] / C - 1.0 >= lim - SEAL_TOL:
                continue
            entry = erow["Open"]
            shares = int((TARGET_CNY / entry) // LOT * LOT)
            if shares < LOT:
                continue
            fills, _ = simulate(g.to_dict("records"), epos, entry, shares)
            cost = entry * shares
            proceeds = sum(sh * px for sh, px, _ in fills)
            net_pct = (proceeds - cost) / cost * 100.0
            by_day[T].append(dict(
                code=code, industry=r["industry"], zt_heat=heat.get(r["industry"], 1),
                amount=amount, sec_mom5=sec_mom5 * 100, sec_mom20=sec_mom20 * 100,
                sec_pct=sec_pct, net_pct=net_pct,
            ))

    all_c = [c for v in by_day.values() for c in v]
    if not all_c:
        sys.exit("no candidates")
    print(f"candidate-days {len(by_day)}   candidates {len(all_c)}")
    print("=" * 66)

    # ---- (1) RANKING: top-2 per day ----
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

    def topk(cands, key, reverse=True):
        return sorted(cands, key=lambda c: c[key], reverse=reverse)[:2]

    rules = [
        ("CURRENT (heat+amount)", current_pick),
        ("sec_mom20 desc", lambda c: topk(c, "sec_mom20")),
        ("sec_mom5  desc", lambda c: topk(c, "sec_mom5")),
        ("sec_pct   desc (hottest sector)", lambda c: topk(c, "sec_pct")),
    ]
    print("(1) RANKING — top-2 per day")
    print(f"{'rule':<34}{'mean%':>8}{'med%':>8}{'win%':>7}{'n':>5}")
    print("-" * 66)
    res = []
    for name, fn in rules:
        nets = np.array([c["net_pct"] for v in by_day.values() for c in fn(v)])
        res.append((name, nets.mean(), np.median(nets), (nets > 0).mean() * 100, len(nets)))
    allnet = np.array([c["net_pct"] for c in all_c])
    res.append(("(take-all baseline)", allnet.mean(), np.median(allnet),
                (allnet > 0).mean() * 100, len(allnet)))
    res.sort(key=lambda r: r[1], reverse=True)
    for name, m, md, w, n in res:
        print(f"{name:<34}{m:>8.2f}{md:>8.2f}{w:>7.0f}{n:>5}")

    # ---- (2) FILTER: does hot-sector subset raise the POOL average? ----
    print("\n(2) FILTER — pool average by sector RS bucket (breadth-compatible)")
    print(f"{'bucket':<34}{'mean%':>8}{'med%':>8}{'win%':>7}{'n':>5}")
    print("-" * 66)
    buckets = [
        ("all eligible", lambda c: True),
        ("sec_pct >= 0.5 (top-half)", lambda c: c["sec_pct"] >= 0.5),
        ("sec_pct >= 0.7 (top-30%)", lambda c: c["sec_pct"] >= 0.7),
        ("sec_pct >= 0.9 (hottest)", lambda c: c["sec_pct"] >= 0.9),
        ("sec_pct <  0.5 (cold half)", lambda c: c["sec_pct"] < 0.5),
    ]
    for name, f in buckets:
        nets = np.array([c["net_pct"] for c in all_c if f(c)])
        if len(nets):
            print(f"{name:<34}{nets.mean():>8.2f}{np.median(nets):>8.2f}"
                  f"{(nets > 0).mean()*100:>7.0f}{len(nets):>5}")


if __name__ == "__main__":
    main()
