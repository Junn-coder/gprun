#!/usr/bin/env python3
"""
US Trend Template backtest — verify frame.md entry/exit rules on historical data.

Entry (all must hold):
  - Trend Template >= 8/9 with #1,#5,#8,#9 mandatory
  - Weinstein Stage 2 (price > rising 30-week MA)
  - Volume >= 1.5x 20-day average volume
  - Close > 20-day high (breakout confirmation)

Exit (first wins):
  - Weekly close below 50-day MA
  - Drawdown > 25% from position high (A档 threshold)
  - Gain >= +100%

Output: per-ticker trade log + aggregate stats (win rate, avg R, total return).

Usage:  python ubacktest.py
"""

import os
import re
import sys
from io import StringIO

import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATADIR = os.path.normpath(os.path.join(HERE, "..", "ushare_data"))


def load(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    hdr = next(i for i, ln in enumerate(lines) if ln.startswith("Date,"))
    df = pd.read_csv(StringIO("".join(lines[hdr:])))
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def slope_up(series, lag):
    s = series.dropna()
    if len(s) <= lag:
        return False
    return s.iloc[-1] > s.iloc[-1 - lag]


def trend_template_ok(df, idx):
    """Check Trend Template 9条 at given index. Returns (passed, mand_ok)."""
    c = df["Close"]
    if idx < 200:
        return (0, False)

    price = c.iloc[idx]
    ma50 = c.iloc[max(0, idx - 49):idx + 1].mean()
    ma150 = c.iloc[max(0, idx - 149):idx + 1].mean()
    ma200 = c.iloc[max(0, idx - 199):idx + 1].mean()

    win = df.iloc[max(0, idx - 251):idx + 1]
    hi52 = win["High"].max()
    lo52 = win["Low"].min()

    # MA slope checks
    m200_1mo_idx = max(0, idx - 21)
    m200_1mo = c.iloc[max(0, m200_1mo_idx - 199):m200_1mo_idx + 1].mean()

    conds = {}
    conds[1] = price > ma150 > ma200
    conds[2] = ma200 > m200_1mo
    conds[3] = ma150 > ma200
    conds[4] = ma50 > ma150 > ma200
    conds[5] = price > ma50
    conds[6] = price >= 1.30 * lo52
    conds[7] = price >= 0.75 * hi52

    # Weekly (#8) — resample on the fly
    sub = df.iloc[:idx + 1].copy()
    sub.index = sub["Date"]
    wk = sub["Close"].resample("W-FRI").last().dropna()
    if len(wk) >= 31:  # need 30w MA + 13w lookback
        wma30 = wk.rolling(30).mean()
        w_above = wk.iloc[-1] > wma30.iloc[-1]
        w_rising = wma30.iloc[-1] > wma30.iloc[-14] if len(wma30) >= 14 else False
        last13 = (wk.iloc[-13:] < wma30.iloc[-13:]).sum()
        conds[8] = w_above and w_rising and (last13 <= 2)
    else:
        conds[8] = False

    # Monthly (#9)
    mo = sub["Close"].resample("ME").last().dropna()
    if len(mo) >= 13:
        mma12 = mo.rolling(12).mean()
        m_above = mo.iloc[-1] > mma12.iloc[-1]
        m_rising = mma12.iloc[-1] > mma12.iloc[-7] if len(mma12) >= 7 else False
        last12 = (mo.iloc[-12:] < mma12.iloc[-12:]).sum()
        conds[9] = m_above and m_rising and (last12 <= 2)
    else:
        conds[9] = False

    computable = {k: v for k, v in conds.items()}
    passed = sum(1 for v in computable.values() if v)
    mand_ok = all(computable.get(k, False) for k in [1, 5, 8, 9])
    return passed, mand_ok


def stage2(df, idx):
    """Weinstein Stage 2: price > rising 30-week MA."""
    sub = df.iloc[:idx + 1].copy()
    sub.index = sub["Date"]
    wk = sub["Close"].resample("W-FRI").last().dropna()
    if len(wk) < 31:
        return False
    wma30 = wk.rolling(30).mean()
    above = wk.iloc[-1] > wma30.iloc[-1]
    rising = wma30.iloc[-1] > wma30.iloc[-14] if len(wma30) >= 14 else False
    return above and rising


def backtest_one(path, ticker):
    df = load(path)
    c = df["Close"]
    v = df["Volume"]
    trades = []

    # State
    in_pos = False
    entry_date = None
    entry_price = 0
    pos_high = 0

    min_idx = 252  # need ~1yr for MA calcs
    for i in range(min_idx, len(df)):
        passed, mand_ok = trend_template_ok(df, i)
        vol_ok = False
        if i >= 20:
            avg20v = v.iloc[i - 20:i].mean()
            vol_ok = v.iloc[i] >= 1.5 * avg20v
        breakout = c.iloc[i] > c.iloc[i - 20:i].max() if i >= 20 else False
        st2 = stage2(df, i)

        entry_signal = (passed >= 8 and mand_ok and st2 and vol_ok and breakout)

        if not in_pos and entry_signal:
            in_pos = True
            entry_date = df["Date"].iloc[i]
            entry_price = c.iloc[i]
            pos_high = c.iloc[i]
            continue

        if not in_pos:
            continue

        # Update position high
        if c.iloc[i] > pos_high:
            pos_high = c.iloc[i]

        # Exit checks
        exit_reason = None
        # 1. Weekly close below 50MA
        ma50 = c.iloc[max(0, i - 49):i + 1].mean()
        dow = df["Date"].iloc[i].dayofweek
        is_fri = dow == 4  # Friday
        if is_fri and c.iloc[i] < ma50:
            exit_reason = "周收<50MA"

        # 2. Drawdown > 25% from position high
        if c.iloc[i] < pos_high * 0.75:
            exit_reason = "回撤>25%"

        # 3. Gain >= +100%
        if c.iloc[i] >= entry_price * 2.0:
            exit_reason = "+100%止盈"

        if exit_reason:
            exit_price = c.iloc[i]
            exit_date = df["Date"].iloc[i]
            pnl_pct = (exit_price / entry_price - 1) * 100
            trades.append({
                "ticker": ticker,
                "entry_date": str(entry_date.date()),
                "exit_date": str(exit_date.date()),
                "entry": round(entry_price, 2),
                "exit": round(exit_price, 2),
                "pnl_pct": round(pnl_pct, 1),
                "reason": exit_reason,
            })
            in_pos = False
            entry_date = None
            entry_price = 0
            pos_high = 0

    return trades


def main():
    files = sorted(f for f in os.listdir(DATADIR) if f.startswith("price_") and f.endswith(".txt"))
    all_trades = []

    for fname in files:
        ticker = fname.replace("price_", "").replace(".txt", "")
        path = os.path.join(DATADIR, fname)
        try:
            trades = backtest_one(path, ticker)
        except Exception as e:
            print(f"  x {ticker}: {e}", file=sys.stderr)
            continue
        if trades:
            all_trades.extend(trades)
            wins = sum(1 for t in trades if t["pnl_pct"] > 0)
            avg_r = np.mean([t["pnl_pct"] for t in trades])
            print(f"  {ticker}: {len(trades)} trades, {wins} wins, avg {avg_r:+.1f}%")
        else:
            print(f"  {ticker}: 0 trades")

    print(f"\n=== Aggregate ({len(all_trades)} trades) ===")
    if not all_trades:
        print("  No trades.")
        return

    wins = sum(1 for t in all_trades if t["pnl_pct"] > 0)
    losses = len(all_trades) - wins
    win_rate = wins / len(all_trades) * 100
    avg_win = np.mean([t["pnl_pct"] for t in all_trades if t["pnl_pct"] > 0]) if wins > 0 else 0
    avg_loss = np.mean([t["pnl_pct"] for t in all_trades if t["pnl_pct"] <= 0]) if losses > 0 else 0
    all_pnl = [t["pnl_pct"] for t in all_trades]

    print(f"  Win rate: {win_rate:.0f}%  ({wins}W/{losses}L)")
    print(f"  Avg win: {avg_win:+.1f}%  Avg loss: {avg_loss:+.1f}%")
    print(f"  Expectancy: {np.mean(all_pnl):+.1f}%")
    print(f"  Max win: {max(all_pnl):+.1f}%  Max loss: {min(all_pnl):+.1f}%")

    # Cumulative return
    cumul = 100.0
    peak = 100.0
    max_dd = 0
    for r in all_pnl:
        cumul *= (1 + r / 100)
        if cumul > peak:
            peak = cumul
        dd = (cumul / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
    total_ret = cumul - 100
    print(f"  Total return: {total_ret:+.1f}%  Max drawdown: {max_dd:.1f}%")

    # Per-ticker breakdown
    print("\n=== Per-ticker ===")
    by_ticker = {}
    for t in all_trades:
        by_ticker.setdefault(t["ticker"], []).append(t["pnl_pct"])
    for tk, pnls in sorted(by_ticker.items()):
        print(f"  {tk}: {len(pnls)} trades, avg {np.mean(pnls):+.1f}%")

    # Per-reason breakdown
    print("\n=== Per exit reason ===")
    by_reason = {}
    for t in all_trades:
        by_reason.setdefault(t["reason"], []).append(t["pnl_pct"])
    for rsn, pnls in sorted(by_reason.items()):
        print(f"  {rsn}: {len(pnls)} exits, avg P&L {np.mean(pnls):+.1f}%")


if __name__ == "__main__":
    main()
