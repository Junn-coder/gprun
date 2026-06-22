#!/usr/bin/env python3
"""
A-stock analysis from cached price files — NO akshare dependency.

Reads share_data/price_<CODE>.txt and prints:
  TREND: MAs, ATR, 52-week range, Stage, swing points
  RISK:  ATR-based stop, 10% stop
  POS:   P&L vs cost, distance to stop, recovery needed

Usage:  python ca.py 002085                        # latest snapshot
        python ca.py 002085 --cost 13.61             # with P&L vs cost
        python ca.py 002085 301387 --cost 13.61,120.60  # multiple tickers

Requires: pandas, numpy (no akshare)
"""

import os, sys, argparse
from io import StringIO
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATADIR = os.path.join(HERE, "share_data")


def load(code):
    path = os.path.join(DATADIR, f"price_{code}.txt")
    if not os.path.exists(path):
        print(f"! {code}: no cached data. Run: python cn_stock.py {code} --history")
        return None
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    hdr = next(i for i, ln in enumerate(lines) if ln.startswith("Date,"))
    df = pd.read_csv(StringIO("".join(lines[hdr:])))
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def analyze(df, cost=None):
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    latest = c.iloc[-1]
    last_date = str(df["Date"].iloc[-1].date())

    # ATR14
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean().iloc[-1]

    # MAs
    mas = {
        "MA5": c.rolling(5).mean().iloc[-1],
        "MA10": c.rolling(10).mean().iloc[-1],
        "MA20": c.rolling(20).mean().iloc[-1],
        "MA50": c.rolling(50).mean().iloc[-1],
        "MA150": c.rolling(150).mean().iloc[-1] if len(c) >= 150 else None,
    }
    ma_all_above = all(latest > v for v in mas.values() if v is not None)

    # 52-week
    hi52 = df["High"].tail(252).max() if len(df) >= 252 else h.max()
    lo52 = df["Low"].tail(252).min() if len(df) >= 252 else l.min()

    # % changes
    chg = {}
    for label, n in [("1m", 21), ("3m", 63), ("6m", 126)]:
        if len(c) > n:
            chg[label] = (latest / c.iloc[-n - 1] - 1) * 100
        else:
            chg[label] = None

    # Stage: 30-week MA
    wk = df.set_index("Date")["Close"].resample("W-FRI").last().dropna()
    if len(wk) >= 31:
        wma30 = wk.rolling(30).mean()
        stage2 = wk.iloc[-1] > wma30.iloc[-1]
        w_rising = wma30.iloc[-1] > wma30.iloc[-14]
    else:
        wma30 = stage2 = w_rising = None

    # Swing points (last 60 days)
    last60 = df.tail(60)
    sw_lows = []
    for i in range(2, len(last60) - 2):
        if all(last60["Low"].iloc[i] < last60["Low"].iloc[i + j] for j in [-2, -1, 1, 2]):
            sw_lows.append((str(last60["Date"].iloc[i].date())[5:], last60["Low"].iloc[i]))
    sw_highs = []
    for i in range(2, len(last60) - 2):
        if all(last60["High"].iloc[i] > last60["High"].iloc[i + j] for j in [-2, -1, 1, 2]):
            sw_highs.append((str(last60["Date"].iloc[i].date())[5:], last60["High"].iloc[i]))

    # Print
    print(f"{'='*50}")
    print(f"  {last_date}  |  现价 ¥{latest:.2f}")
    print(f"{'='*50}")

    # Trend section
    print(f"  ATR14: ¥{atr14:.2f} ({atr14/latest*100:.1f}%)")
    ma_str = "  ".join(f"{k}:¥{v:.2f}" for k, v in mas.items() if v is not None)
    print(f"  {ma_str}")
    print(f"  价在MA之上: {'YES' if ma_all_above else 'NO'}")
    print(f"  52周 高:¥{hi52:.2f}  低:¥{lo52:.2f}")
    print(f"  距高:{(latest/hi52-1)*100:+.1f}%  距低:{(latest/lo52-1)*100:+.1f}%")
    chg_str = "  ".join(f"{k}:{v:+.1f}%" for k, v in chg.items() if v is not None)
    print(f"  {chg_str}")

    if wma30 is not None:
        print(f"  30周MA: ¥{wma30.iloc[-1]:.2f}  Stage2: {stage2}  上行: {w_rising}")

    if sw_lows:
        print(f"  支撑: " + " | ".join(f"{d} ¥{v:.2f}" for d, v in sw_lows[-2:]))
    if sw_highs:
        print(f"  阻力: " + " | ".join(f"{d} ¥{v:.2f}" for d, v in sw_highs[-2:]))

    # Risk section
    print(f"  {'─'*40}")
    stop_atr = latest - 2 * atr14
    stop_10pct = latest * 0.9
    print(f"  ATR止损(2x): ¥{stop_atr:.2f}")
    print(f"  10%硬止损:    ¥{stop_10pct:.2f}")

    # Position section
    if cost:
        pnl_pct = (latest / cost - 1) * 100
        pnl_amt = 0  # caller provides shares if needed
        recovery = (cost / latest - 1) * 100
        print(f"  {'─'*40}")
        print(f"  成本: ¥{cost:.2f}  浮盈: {pnl_pct:+.1f}%")
        print(f"  回本需涨: {recovery:+.1f}%")

    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="+")
    ap.add_argument("--cost", type=str, help="entry costs, comma-separated, 1 per code: --cost 13.61,120.60")
    args = ap.parse_args()

    costs = [None] * len(args.codes)
    if args.cost:
        vals = [float(x.strip()) for x in args.cost.split(",")]
        for i, v in enumerate(vals):
            if i < len(costs):
                costs[i] = v

    for code, cost in zip(args.codes, costs):
        df = load(code)
        if df is not None:
            analyze(df, cost)


if __name__ == "__main__":
    main()
