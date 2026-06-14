#!/usr/bin/env python3
"""Phase E: Test the 5+ consecutive board auto-exclusion (framed.md §2C).

Question: Do stocks with 5+ consecutive boards still show positive forward
expectancy in a warm-up market, or is the absolute exclusion justified?

Method: Detect limit-up streaks from 948 stocks' OHLCV history, compute
forward returns, and compare 5+ board vs 1-4 board entries.
"""

import os
import sys
from collections import defaultdict
from pathlib import Path

STOCK_DIR = Path(__file__).resolve().parent / "stock_history_ak"


def board_limit(symbol: str) -> float:
    """Return the daily limit-up percentage for a given stock code."""
    code = symbol[:6]
    if code.startswith("30"):  # ChiNext
        return 0.20
    if code.startswith("68"):  # STAR
        return 0.20
    return 0.10  # Main board


def is_limit_up(close: float, prev_close: float, limit_pct: float) -> bool:
    """Check if close is at/near the limit-up price, accounting for rounding."""
    if prev_close <= 0 or close <= 0:
        return False
    limit_price = round(prev_close * (1 + limit_pct), 2)
    # Within 0.02 tolerance for rounding artifacts
    return close >= limit_price - 0.02


def load_stock(stock_path: Path):
    """Load a stock CSV, return list of (date, close, prev_close)."""
    rows = []
    with open(stock_path) as f:
        header = f.readline().strip()
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 5:
                continue
            date = parts[0]
            try:
                close = float(parts[4])
            except ValueError:
                continue
            rows.append((date, close))
    return rows


def detect_streaks(rows, limit_pct):
    """Detect consecutive limit-up streaks from price data.
    
    Returns list of (start_idx, board_count, entry_date) for each streak.
    """
    streaks = []
    i = 1
    while i < len(rows):
        date, close = rows[i]
        _, prev_close = rows[i - 1]
        if is_limit_up(close, prev_close, limit_pct):
            # Found a limit-up; count consecutive
            board_count = 1
            j = i
            while j + 1 < len(rows):
                _, next_close = rows[j + 1]
                _, this_close = rows[j]
                if is_limit_up(next_close, this_close, limit_pct):
                    board_count += 1
                    j += 1
                else:
                    break
            # Record each board day's entry point (for forward-return analysis)
            # We enter on the day the board count FIRST reaches N
            # Only record the day it hits board_count (the last day of the streak)
            streaks.append((j, board_count, rows[j][0]))
            i = j + 1
        else:
            i += 1
    return streaks


def forward_return(rows, start_idx, days):
    """Compute forward return over `days` trading days from start_idx.
    
    Returns (pct_return, reached) — pct_return uses entry close vs exit close.
    If not enough data, returns (None, False).
    """
    if start_idx + days >= len(rows):
        return None, False
    entry_close = rows[start_idx][1]
    exit_close = rows[start_idx + days][1]
    if entry_close <= 0:
        return None, False
    return (exit_close / entry_close - 1) * 100, True


def compute_simple_gate(index_rows, date):
    """Simple MA-based gate: 5-day vs 10-day MA of index close.
    
    Returns 'GREEN' if close > 5MA > 10MA and 5MA sloping up.
    Returns 'AMBER' if close < 10MA but > 5MA or mixed.
    Returns 'RED' if close < both MAs or 5MA < 10MA downward.
    """
    # Find date index
    idx = None
    for i, (d, c) in enumerate(index_rows):
        if d == date:
            idx = i
            break
    if idx is None or idx < 10:
        return None  # Insufficient data
    
    closes = [index_rows[j][1] for j in range(idx - 9, idx + 1)]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes) / 10
    prev_ma5 = sum(closes[-6:-1]) / 5
    
    close = closes[-1]
    ma5_sloping_up = ma5 > prev_ma5
    
    if close > ma5 and close > ma10 and ma5 > ma10 and ma5_sloping_up:
        return "GREEN"
    elif close < ma10 and close > ma5:
        return "AMBER"
    elif close < ma5 and close < ma10:
        return "RED"
    else:
        return "AMBER"


def main():
    print("=" * 70)
    print(" Phase E: 5+ Consecutive Board Exclusion Test")
    print("=" * 70)
    
    # Load index for gate computation (use 000001 SSE Composite)
    index_path = STOCK_DIR / "000001.csv"
    if not index_path.exists():
        print("ERROR: No index data (000001.csv) in stock_history_ak")
        sys.exit(1)
    index_rows = load_stock(index_path)
    print(f"  Index data: {len(index_rows)} days ({index_rows[0][0]} → {index_rows[-1][0]})")
    
    # Process all stocks
    stock_files = sorted(STOCK_DIR.glob("*.csv"))
    print(f"  Stocks to process: {len(stock_files)}")
    
    # Results buckets
    results_5plus = []   # (ret5, ret10, board_count, gate, symbol)
    results_1to4 = []    # same for 1-4 boards
    
    stats = {"total_streaks": 0, "stocks_with_streaks": 0, "skipped_no_data": 0}
    
    for sf in stock_files:
        symbol = sf.stem
        limit_pct = board_limit(symbol)
        rows = load_stock(sf)
        if len(rows) < 20:
            stats["skipped_no_data"] += 1
            continue
        
        streaks = detect_streaks(rows, limit_pct)
        if not streaks:
            continue
        
        stats["stocks_with_streaks"] += 1
        
        for streak_idx, board_count, entry_date in streaks:
            stats["total_streaks"] += 1
            
            # Compute forward returns
            ret5, ok5 = forward_return(rows, streak_idx, 5)
            ret10, ok10 = forward_return(rows, streak_idx, 10)
            
            if not ok5 and not ok10:
                continue
            
            # Get gate state
            gate = compute_simple_gate(index_rows, entry_date)
            if gate is None:
                continue
            
            entry = (ret5, ret10, board_count, gate, symbol)
            if board_count >= 5:
                results_5plus.append(entry)
            else:
                results_1to4.append(entry)
    
    print(f"  Stocks with streaks: {stats['stocks_with_streaks']}")
    print(f"  Total streaks detected: {stats['total_streaks']}")
    print(f"  1-4 board entries: {len(results_1to4)}")
    print(f"  5+ board entries: {len(results_5plus)}")
    print()
    
    # --- Analysis ---
    def summarize(data, label):
        if not data:
            print(f"  {label}: NO DATA")
            return
        
        ret5s = [r[0] for r in data if r[0] is not None]
        ret10s = [r[1] for r in data if r[1] is not None]
        
        print(f"  --- {label} (n={len(data)}) ---")
        if ret5s:
            print(f"    5-day:  mean={sum(ret5s)/len(ret5s):+.2f}%  win%={sum(1 for r in ret5s if r>0)/len(ret5s)*100:.1f}%  "
                  f"median={sorted(ret5s)[len(ret5s)//2]:+.2f}%  min={min(ret5s):+.2f}%  max={max(ret5s):+.2f}%")
        if ret10s:
            print(f"    10-day: mean={sum(ret10s)/len(ret10s):+.2f}%  win%={sum(1 for r in ret10s if r>0)/len(ret10s)*100:.1f}%  "
                  f"median={sorted(ret10s)[len(ret10s)//2]:+.2f}%  min={min(ret10s):+.2f}%  max={max(ret10s):+.2f}%")
        
        # By gate
        for gate in ["GREEN", "AMBER", "RED"]:
            gdata = [r for r in data if r[3] == gate]
            if not gdata:
                continue
            gret5 = [r[0] for r in gdata if r[0] is not None]
            gret10 = [r[1] for r in gdata if r[1] is not None]
            print(f"    {gate} (n={len(gdata)}): ", end="")
            if gret5:
                print(f"5d={sum(gret5)/len(gret5):+.2f}% (win={sum(1 for r in gret5 if r>0)/len(gret5)*100:.0f}%)", end="  ")
            if gret10:
                print(f"10d={sum(gret10)/len(gret10):+.2f}% (win={sum(1 for r in gret10 if r>0)/len(gret10)*100:.0f}%)", end="")
            print()
        print()
    
    summarize(results_1to4, "1-4 CONSECUTIVE BOARD ENTRIES")
    summarize(results_5plus, "5+ CONSECUTIVE BOARD ENTRIES")
    
    # --- Per-board-count breakdown ---
    print("  --- Per-board-count breakdown ---")
    by_count = defaultdict(list)
    for lst in [results_1to4, results_5plus]:
        for r in lst:
            by_count[r[2]].append(r)
    for bc in sorted(by_count.keys()):
        data = by_count[bc]
        ret5s = [r[0] for r in data if r[0] is not None]
        ret10s = [r[1] for r in data if r[1] is not None]
        r5 = f"{sum(ret5s)/len(ret5s):+.2f}%" if ret5s else "N/A"
        r10 = f"{sum(ret10s)/len(ret10s):+.2f}%" if ret10s else "N/A"
        print(f"    {bc:2d}-board (n={len(data):4d}):  5d={r5}  10d={r10}")
    
    # --- Key: 5+ board entries in GREEN/AMBER only (warm market) ---
    print()
    print("  --- 5+ board entries in BUYABLE market (GREEN+AMBER) ---")
    warm_5plus = [r for r in results_5plus if r[3] in ("GREEN", "AMBER")]
    if warm_5plus:
        ret5s = [r[0] for r in warm_5plus if r[0] is not None]
        ret10s = [r[1] for r in warm_5plus if r[1] is not None]
        print(f"    n={len(warm_5plus)}")
        if ret5s:
            print(f"    5-day:  mean={sum(ret5s)/len(ret5s):+.2f}%  win%={sum(1 for r in ret5s if r>0)/len(ret5s)*100:.1f}%")
        if ret10s:
            print(f"    10-day: mean={sum(ret10s)/len(ret10s):+.2f}%  win%={sum(1 for r in ret10s if r>0)/len(ret10s)*100:.1f}%")
    else:
        print("    NO DATA")
    
    print()
    print("=" * 70)
    print(" Phase E complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
