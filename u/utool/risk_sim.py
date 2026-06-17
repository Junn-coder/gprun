#!/usr/bin/env python3
"""
Risk-level simulation: run uentry_backtest breakout trades through different
risk-per-trade percentages, show cumulative account growth.

Answer: "if I risk 1% vs 2% vs 3%..., what does my $11K become?"
"""

import os, sys, re
import json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from uentry_backtest import (
    load, indicators, breakout_signal, backtest,
    PIVOT_LOOKBACK, VOL_MULT, MAX_VOL_MULT, MAX_DIST_MA50,
    STOP_PCT, MAX_HOLD, OUTDIR,
)

# --- 1. Collect all breakout trade returns ---
def collect_breakout_returns():
    files = [os.path.join(OUTDIR, f) for f in sorted(os.listdir(OUTDIR))
             if re.match(r"^price_.+\.txt$", f)]
    all_rets = []
    for path in files:
        if not os.path.exists(path):
            continue
        t = re.match(r"^price_(.+)\.txt$", os.path.basename(path)).group(1).upper()
        df = load(path)
        if len(df) < 200:
            continue
        df = indicators(df)
        bo = backtest(df, breakout_signal)
        for tr in bo:
            all_rets.append(tr["ret"])  # % return on the trade
    return all_rets


# --- 2. Simulate compounding for a given risk % ---
def simulate(trade_rets, risk_pct, start_capital=11000):
    """
    Each trade return is relative to entry.  Stop is STOP_PCT (8%).
    Position size = capital * risk_pct / STOP_PCT.
    Account return from trade = trade_ret% * (risk_pct / STOP_PCT).
    """
    cap = start_capital
    equity_curve = [cap]
    for ret in trade_rets:
        account_ret = ret * (risk_pct / (STOP_PCT * 100))
        cap *= (1 + account_ret / 100)
        equity_curve.append(round(cap, 2))
    return equity_curve


# --- 3. Main ---
def main():
    rets = collect_breakout_returns()
    print(f"= 突破买入 {len(rets)} 笔交易序列 (按时间)")
    print(f"  收益序列: {[round(r,1) for r in rets[:10]]}... (前10笔)")

    risks = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0]
    start = 11000

    print(f"\n{'风险/笔':>8}  {'终值':>10}  {'总收益%':>8}  {'最大回撤%':>8}  {'连亏次数':>6}")
    print("-" * 55)

    best_risk = None
    best_final = 0

    for rp in risks:
        curve = simulate(rets, rp, start)
        final = curve[-1]
        total_ret = (final / start - 1) * 100
        # Max drawdown
        peak = curve[0]
        max_dd = 0
        for v in curve:
            if v > peak:
                peak = v
            dd = (v / peak - 1) * 100
            if dd < max_dd:
                max_dd = dd
        # Max consecutive losers
        max_lose_streak = 0
        cur_streak = 0
        for r in rets:
            if r <= 0:
                cur_streak += 1
                max_lose_streak = max(max_lose_streak, cur_streak)
            else:
                cur_streak = 0

        marker = ""
        if final > best_final:
            best_final = final
            best_risk = rp
            marker = " ← 最高终值"

        print(f"  {rp:5.1f}%  ${final:>9,.0f}  {total_ret:>+7.1f}%  {max_dd:>+7.1f}%  {max_lose_streak:>6}{marker}")

    print(f"\n注: 回测不含大盘闸门、佣金/滑点，实盘结果会更低。")
    print(f"    frame.md 当前 1.5%，实盘验证后目标 2%。")
    print(f"    建议: 每 20 笔验证一次，持续达标后切 2%。")


if __name__ == "__main__":
    main()
