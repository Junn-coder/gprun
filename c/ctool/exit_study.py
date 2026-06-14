#!/usr/bin/env python3
"""
exit_study.py — STAGE A for the SELL side (framed.md §4, "the spine").

We exhaustively tested ENTRY-side levers (ranking, sector filter) — all noise/regime. We never
tested the EXIT rules. This measures, on the POOL of all eligible candidates (~400-560 trades,
high-n so per-trade effects are visible above variance), how different exit configs change the
realized outcome. Includes J's idea: drop the day-3 time-stop, and add a VOLUME-blow-off exit
(sell when volume becomes very large; hold while rising on low volume) — pure-spike and
spike+stall (放量滞涨) variants.

A config only counts if it beats BASE on TRAIN *and* the untouched VALIDATION half.

Usage:
    python exit_study.py                                       # TRAIN
    python exit_study.py --start 2025-07-01 --end 2026-05-29   # VALIDATION
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
    load_history, annotate, load_meta, board_limit,
    CAP_LOW, CAP_HIGH, TARGET_CNY, LOT, SEAL_TOL,
)


def vol_spike(series, pos, vx):
    N, K = vx["N"], vx["K"]
    if pos - N < 0:
        return False
    avg = np.mean([series[j]["Volume"] for j in range(pos - N, pos)])
    if not np.isfinite(avg) or avg <= 0:
        return False
    hit = series[pos]["Volume"] >= K * avg
    if vx.get("stall"):
        hit = hit and (series[pos]["Close"] < series[pos]["Open"])   # big volume + down close = distribution
    return hit


def simulate_cfg(series, epos, entry, shares, cfg):
    """Configurable framed.md §4 exit engine. Returns net P&L % (after no fees here — fees are
    a constant ~0.085% offset that doesn't change the RANKING of configs)."""
    stop = cfg.get("stop")              # .07 or None
    tp = cfg.get("tp")                  # (tp1, frac, tp2) or None  (None = ride, no take-profit)
    trail = cfg.get("trail", False)
    tstop = cfg.get("time_stop")        # (day, gain) or None
    vexit = cfg.get("vol_exit")         # dict(K,N,stall) or None
    maxhold = cfg.get("maxhold", 10)

    fills, remaining = [], shares
    tp1_done = armed = False
    pending = None
    n, pos = 0, epos
    while pos < len(series) and remaining > 0:
        row = series[pos]
        n += 1
        O, H, C = row["Open"], row["High"], row["Close"]
        if pending is not None:
            fills.append((remaining, O)); remaining = 0; break
        if tp:
            tp1, frac, tp2 = tp
            if not tp1_done and H >= entry * (1 + tp1):
                half = int((shares * frac) // LOT) * LOT
                if half > 0:
                    fills.append((half, entry * (1 + tp1))); remaining -= half
                tp1_done = True
            if remaining > 0 and H >= entry * (1 + tp2):
                fills.append((remaining, entry * (1 + tp2))); remaining = 0; break
        gain_c = C / entry - 1.0
        if trail and gain_c > 0.10:
            armed = True
        reason = None
        if stop and C <= entry * (1 - stop):
            reason = "stop"
        elif trail and armed and not pd.isna(row["ma5"]) and C < row["ma5"]:
            reason = "trail"
        elif tstop and n == tstop[0] and gain_c < tstop[1]:
            reason = "time"
        elif vexit and vol_spike(series, pos, vexit):
            reason = "vol"
        elif n >= maxhold:
            reason = "maxhold"
        if reason:
            pending = reason
        pos += 1
    if remaining > 0:                                   # ran off the data: close at last close
        fills.append((remaining, series[min(pos, len(series) - 1)]["Close"]))
    proceeds = sum(sh * px for sh, px in fills)
    cost = entry * shares
    return (proceeds - cost) / cost * 100.0


CONFIGS = [
    ("BASE  stop7 TS TP trail",       dict(stop=.07, tp=(.08, .5, .15), trail=True, time_stop=(3, .03))),
    ("noTS  stop7 TP trail",          dict(stop=.07, tp=(.08, .5, .15), trail=True)),
    ("noTS  stop7 TP trail volK2",    dict(stop=.07, tp=(.08, .5, .15), trail=True, vol_exit=dict(K=2.0, N=5))),
    ("noTS  stop7 TP trail volK2.5s", dict(stop=.07, tp=(.08, .5, .15), trail=True, vol_exit=dict(K=2.5, N=5, stall=True))),
    ("RIDE  noTS stop7 trail (noTP)", dict(stop=.07, trail=True)),
    ("RIDE  noTS stop7 trail volK2",  dict(stop=.07, trail=True, vol_exit=dict(K=2.0, N=5))),
    ("RIDE  noTS stop7 trail volK2.5s", dict(stop=.07, trail=True, vol_exit=dict(K=2.5, N=5, stall=True))),
    ("noTS  stop5 TP trail",          dict(stop=.05, tp=(.08, .5, .15), trail=True)),
    ("noTS  stop10 TP trail",         dict(stop=.10, tp=(.08, .5, .15), trail=True)),
]


def main():
    ap = argparse.ArgumentParser(description="Stage-A exit-rule diagnostic (pool)")
    ap.add_argument("--start", default="2024-05-28")
    ap.add_argument("--end", default="2025-06-30")
    args = ap.parse_args()

    gseries = load_series(DEFAULT_OUTDIR)
    green = [d for d, v in replay(gseries, args.start, args.end) if v == "GREEN"]
    print(f"GREEN days: {len(green)}")

    hist = annotate(load_history())
    meta = load_meta()
    by_code = {c: g.reset_index(drop=True) for c, g in hist.groupby("symbol", sort=False)}
    all_dates = sorted(hist["Date"].unique())

    def next_date(d):
        i = all_dates.index(d) if d in all_dates else None
        return all_dates[i + 1] if (i is not None and i + 1 < len(all_dates)) else None

    # collect every eligible candidate's (series, epos, entry, shares) once
    trades = []
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
        elig = s[(s["board_count"] == 1) & (s["cap"].between(CAP_LOW, CAP_HIGH))]
        T1 = next_date(T)
        for _, r in elig.iterrows():
            code = r["symbol"]
            g = by_code[code]
            er = g.index[g["Date"] == T1].tolist() if T1 else []
            if not er:
                continue
            epos = er[0]
            erow = g.loc[epos]
            lim = board_limit(code)
            if erow["Open"] / r["Close"] - 1.0 >= lim - SEAL_TOL:
                continue
            entry = erow["Open"]
            shares = int((TARGET_CNY / entry) // LOT * LOT)
            if shares < LOT:
                continue
            trades.append((g.to_dict("records"), epos, entry, shares))

    print(f"pool trades: {len(trades)}")
    print("=" * 64)
    print(f"{'exit config':<34}{'mean%':>8}{'med%':>8}{'win%':>7}{'n':>6}")
    print("-" * 64)
    base_mean = None
    for name, cfg in CONFIGS:
        nets = np.array([simulate_cfg(s, e, en, sh, cfg) for s, e, en, sh in trades])
        if base_mean is None:
            base_mean = nets.mean()
        d = nets.mean() - base_mean
        tag = "  BASE" if name.startswith("BASE") else f"  ({d:+.2f} vs base)"
        print(f"{name:<34}{nets.mean():>8.2f}{np.median(nets):>8.2f}"
              f"{(nets > 0).mean()*100:>7.0f}{len(nets):>6}{tag}")


if __name__ == "__main__":
    main()
