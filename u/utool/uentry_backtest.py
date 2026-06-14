#!/usr/bin/env python3
"""
Entry-method A/B backtest: 突破买入(breakout) vs 回踩买入(pullback).

Question: is frame's "放量突破才进" actually a good US entry, vs buying pullbacks?
Method: hold EXIT rules identical for both strategies, vary only the ENTRY, run over
~2y of daily bars for the watchlist tickers, and compare win rate / profit factor /
expectancy. Identical exits => any difference is attributable to the entry.

ENTRY A — breakout (frame §3B 放量突破):
  signal bar i: Close[i] > pivot  AND  Volume[i] >= VOL_MULT * 20d avg vol
                pivot = highest High of the prior PIVOT_LOOKBACK bars (excludes bar i)
  (only when in an uptrend: Close>MA50>MA150, to match frame's Stage-2 precondition)

ENTRY B — pullback (frame §3A 回踩 50 日不破 + 放量收阳):
  uptrend same as above; price dips to the rising 50MA (Low[i] <= MA50*1.02) but holds
  (Close[i] > MA50[i]) and turns up (Close[i] > Close[i-1]) on above-average volume.

FILL (both, no look-ahead): enter at next bar's Open (you see the signal at close, buy
next open). One position per ticker at a time; re-scan for the next signal once flat.

EXIT (identical for both, isolates the entry):
  - hard initial stop: entry * (1 - STOP_PCT)  [STOP_PCT=8%], filled at stop (or gap Open)
  - trend exit: Close < MA50  -> exit at that Close (frame: 趋势没破不动手, 50MA loose trail)
  - time cap: MAX_HOLD bars    -> exit at Close
  first to trigger wins.

METRICS per strategy (pooled across tickers):
  trades, win%, avg win%, avg loss%, profit factor (Σwin / Σ|loss|),
  avg R (return / STOP_PCT), expectancy (avg return per trade).

HONEST CAVEATS (printed): small sample; tickers were picked BECAUSE they're current
strong names -> selection bias favors trend strategies; no commission/slippage in the
headline numbers (a 0.2% round-trip sensitivity is printed separately); no market-regime
(frame 第五层) gate -> this measures entry tactic only, not the whole system.
"""

import os
import re
import sys
import argparse

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUTDIR = os.path.normpath(os.path.join(HERE, "..", "ushare_data"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from utrend import load

# --- tunables ---
PIVOT_LOOKBACK = 40
VOL_MULT = 1.5
STOP_PCT = 0.08
MAX_HOLD = 60
ROUNDTRIP_COST = 0.002   # 0.2% sensitivity only


def indicators(df):
    c = df["Close"]
    df = df.copy()
    df["MA20"] = c.rolling(20).mean()
    df["MA50"] = c.rolling(50).mean()
    df["MA150"] = c.rolling(150).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["PIVOT"] = df["High"].rolling(PIVOT_LOOKBACK).max().shift(1)  # prior-bars high, excludes today
    return df


def uptrend(r):
    return (r.Close > r.MA50 > r.MA150) and pd.notna(r.MA150)


def breakout_signal(df, i):
    r = df.iloc[i]
    if not uptrend(r) or pd.isna(r.PIVOT) or pd.isna(r.VOL20):
        return False
    return (r.Close > r.PIVOT) and (r.Volume >= VOL_MULT * r.VOL20)


def pullback_signal(df, i):
    if i < 1:
        return False
    r, p = df.iloc[i], df.iloc[i - 1]
    if not uptrend(r) or pd.isna(r.VOL20):
        return False
    touched = r.Low <= r.MA50 * 1.02       # dipped to/near the 50MA
    held = r.Close > r.MA50                 # but closed back above it
    turned = r.Close > p.Close              # up day (confirmation)
    vol_ok = r.Volume >= r.VOL20            # above-average volume on the turn
    return touched and held and turned and vol_ok


def run_trade(df, entry_i):
    """Enter at Open of bar entry_i; return (ret_pct, bars_held, reason) or None."""
    if entry_i >= len(df):
        return None
    entry = df.iloc[entry_i]["Open"]
    if pd.isna(entry) or entry <= 0:
        return None
    stop = entry * (1 - STOP_PCT)
    for j in range(entry_i, min(entry_i + MAX_HOLD, len(df))):
        bar = df.iloc[j]
        # hard stop (intraday). gap-through fills at the open.
        if bar.Low <= stop:
            fill = min(bar.Open, stop) if bar.Open < stop else stop
            return (fill / entry - 1) * 100, j - entry_i, "stop", j
        # trend exit on close < 50MA (skip the entry bar itself)
        if j > entry_i and pd.notna(bar.MA50) and bar.Close < bar.MA50:
            return (bar.Close / entry - 1) * 100, j - entry_i, "MA50", j
    # time cap
    last = df.iloc[min(entry_i + MAX_HOLD, len(df)) - 1]
    return (last.Close / entry - 1) * 100, min(MAX_HOLD, len(df) - entry_i) - 1, "time", None


def backtest(df, signal_fn):
    """Walk bars; when flat and signal fires, enter NEXT open; collect trades."""
    trades = []
    i = 150  # warmup for MA150
    n = len(df)
    while i < n - 1:
        if signal_fn(df, i):
            res = run_trade(df, i + 1)   # fill next open
            if res is None:
                break
            ret, held, reason, exit_j = res
            trades.append({"ret": ret, "held": held, "reason": reason})
            i = (exit_j + 1) if exit_j is not None else (i + 1 + held + 1)  # resume after exit
        else:
            i += 1
    return trades


def summarize(name, trades):
    if not trades:
        print(f"\n[{name}] 0 笔交易")
        return
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    net = rets - ROUNDTRIP_COST * 100
    print(f"\n[{name}]  {len(trades)} 笔")
    print(f"  胜率        : {len(wins)/len(rets)*100:5.1f}%   ({len(wins)}赢 / {len(losses)}亏)")
    print(f"  平均盈利    : {wins.mean() if len(wins) else 0:+6.2f}%")
    print(f"  平均亏损    : {losses.mean() if len(losses) else 0:+6.2f}%")
    print(f"  盈亏比 PF   : {pf:5.2f}   (Σ赢 / Σ亏)")
    print(f"  平均每笔    : {rets.mean():+6.2f}%   (期望)")
    print(f"  平均 R      : {rets.mean()/ (STOP_PCT*100):+5.2f} R   (每笔/初始8%风险)")
    print(f"  扣0.2%成本后: {net.mean():+6.2f}% 每笔")
    print(f"  最大单亏    : {rets.min():+6.2f}%   最大单赢: {rets.max():+6.2f}%")
    by = {}
    for t in trades:
        by[t["reason"]] = by.get(t["reason"], 0) + 1
    print(f"  出场构成    : {by}")
    return rets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="default: all price_*.txt")
    ap.add_argument("--outdir", default=OUTDIR)
    args = ap.parse_args()

    if args.tickers:
        files = [os.path.join(args.outdir, f"price_{t.upper()}.txt") for t in args.tickers]
    else:
        files = [os.path.join(args.outdir, f) for f in sorted(os.listdir(args.outdir))
                 if re.match(r"^price_.+\.txt$", f)]

    all_bo, all_pb = [], []
    per_ticker = []
    for path in files:
        if not os.path.exists(path):
            continue
        t = re.match(r"^price_(.+)\.txt$", os.path.basename(path)).group(1).upper()
        df = load(path)
        if len(df) < 200:
            continue
        df = indicators(df)
        bo = backtest(df, breakout_signal)
        pb = backtest(df, pullback_signal)
        all_bo += bo
        all_pb += pb
        per_ticker.append((t, len(bo), len(pb)))

    print("=" * 64)
    print(f" 入场对照回测  {len(files)} 只标的  (近 ~2 年日线)")
    print(f" 参数: pivot={PIVOT_LOOKBACK}日高, 量≥{VOL_MULT}×20日, 止损-{STOP_PCT*100:.0f}%, "
          f"趋势出场 close<50MA, 时间上限 {MAX_HOLD} 日")
    print("=" * 64)
    print(" 每标的信号数 (突破 / 回踩):")
    for t, nb, np_ in per_ticker:
        print(f"   {t:<6} {nb:>3} / {np_:>3}")

    summarize("突破买入 BREAKOUT", all_bo)
    summarize("回踩买入 PULLBACK", all_pb)

    print("\n" + "-" * 64)
    print("诚实提醒: 样本小; 标的是当下强势票->选择偏差利好趋势策略; 头条数字未含佣金/滑点")
    print("(已附 0.2% 成本敏感行); 未加大盘闸门(frame 第五层)-> 只测入场战术, 非整套系统.")


if __name__ == "__main__":
    main()
