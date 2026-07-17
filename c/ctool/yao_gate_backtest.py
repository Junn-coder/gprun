#!/usr/bin/env python3
"""
Yao gate backtest — compare 3 gate variants for the yao (妖股) method.

Variants:
  A: Current baseline  (0 RED, 1-2 AMBER, 3+ GREEN, index crash RED)
  B: Looser            (0 AMBER, 1-2 GREEN max 2, 3+ GREEN max 3, index crash RED)
  C: Hybrid            (3+ GREEN, 1-2 AMBER, 0 + hot market AMBER, 0 + cold RED, crash RED)

Uses flat_yao CSV data; no network calls.
"""

import os
import re
import sys
import random
from collections import defaultdict, Counter
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FLAT_YAO_DIR = os.path.join(ROOT, "flat_yao")
YAOLIST_PATH = os.path.join(ROOT, "yao", "yaolist.md")
INDEX_CACHE_DIR = os.path.join(HERE, "share_data")

POSITION_SIZE = 5000
START_DATE = "2026-01-01"
END_DATE = "2026-07-16"
RANDOM_SEED = 42

random.seed(RANDOM_SEED)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def parse_yao_codes(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    codes = re.findall(r"\|\s*\d+\s*\|\s*(\d{6})\s*\|", text)
    return list(dict.fromkeys(codes))  # dedup, keep order


def load_csv(path):
    """Load a flat_yao CSV, normalise Date, return DataFrame or None."""
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if df.empty:
        return None
    df["Date"] = df["Date"].astype(str)
    # drop rows that don't look like dates YYYY-MM-DD
    df = df[df["Date"].str.match(r"^\d{4}-\d{2}-\d{2}$")]
    if df.empty:
        return None
    # sort by date
    df = df.sort_values("Date").reset_index(drop=True)
    # ensure numeric columns
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def detect_lu(df):
    """
    Return a set of dates (YYYY-MM-DD strings) where the stock hit limit-up.
    10% board: 0.095 <= (close - prev_close) / prev_close <= 0.105
    """
    lu_dates = set()
    closes = df["Close"].values
    dates = df["Date"].values
    for i in range(1, len(closes)):
        prev_c = closes[i - 1]
        cur_c = closes[i]
        if prev_c <= 0:
            continue
        chg = (cur_c - prev_c) / prev_c
        if 0.095 <= chg <= 0.105:
            lu_dates.add(str(dates[i]))
    return lu_dates


def is_yiziban(df, date_str):
    """Check if the stock gaps up >9.5% at open on the given date."""
    rows = df[df["Date"] == date_str]
    if rows.empty:
        return False
    idx = rows.index[0]
    if idx == 0:
        return False
    prev_close = df.loc[idx - 1, "Close"]
    today_open = rows["Open"].iloc[0]
    if pd.isna(prev_close) or pd.isna(today_open) or prev_close <= 0:
        return False
    return (today_open / prev_close - 1) > 0.095


# ──────────────────────────────────────────────
# Index crash detection (from cached files)
# ──────────────────────────────────────────────

def load_index_cache():
    """Load index daily data from share_data/index_*.txt files.
    Returns {code: DataFrame} for 000001, 000300, 399006."""
    index_map = {}
    for code in ["000001", "000300", "399006"]:
        path = os.path.join(INDEX_CACHE_DIR, f"index_{code}.txt")
        if not os.path.exists(path):
            continue
        try:
            rows = []
            with open(path, encoding="utf-8") as f:
                in_data = False
                for line in f:
                    line = line.strip()
                    if not in_data:
                        if line.startswith("Date,"):
                            in_data = True
                        continue
                    if line and not line.startswith("#") and not line.startswith("Range") and not line.startswith("Total") and not line.startswith("Source") and line:
                        parts = line.split(",")
                        if len(parts) >= 5:
                            rows.append({"Date": parts[0], "Close": float(parts[2])})
            if rows:
                df = pd.DataFrame(rows)
                df["Date"] = df["Date"].astype(str)
                df = df.sort_values("Date").reset_index(drop=True)
                index_map[code] = df
        except Exception:
            continue
    return index_map


def build_index_crash_set(index_map):
    """Return set of dates where any index dropped >3%."""
    crash_dates = set()
    for code, df in index_map.items():
        closes = df["Close"].values
        dates = df["Date"].values
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            cur = closes[i]
            if prev <= 0:
                continue
            chg = (cur / prev - 1) * 100
            if chg < -3:
                crash_dates.add(str(dates[i]))
    return crash_dates


# ──────────────────────────────────────────────
# Market-wide LU count (for variant C)
# ──────────────────────────────────────────────

def build_market_lu_map(flat_dir, start, end):
    """Pre-compute total market LU count for each day in [start, end].
    Returns dict {date: count}."""
    print("  Pre-computing market-wide LU counts (this may take ~1-2 min)...", flush=True)
    lu_counter = Counter()
    csv_files = sorted(Path(flat_dir).glob("*.csv"))
    total = len(csv_files)
    for i, fp in enumerate(csv_files):
        if i % 500 == 0 and i > 0:
            print(f"    processed {i}/{total} files...", flush=True)
        df = load_csv(str(fp))
        if df is None:
            continue
        # Filter to relevant date range
        df = df[(df["Date"] >= start) & (df["Date"] <= end)]
        if len(df) < 2:
            continue
        dates = df["Date"].values
        closes = df["Close"].values
        for j in range(1, len(closes)):
            prev_c = closes[j - 1]
            cur_c = closes[j]
            if prev_c <= 0:
                continue
            chg = (cur_c - prev_c) / prev_c
            if 0.095 <= chg <= 0.105:
                lu_counter[str(dates[j])] += 1
    print(f"    done — {total} files processed.", flush=True)
    return dict(lu_counter)


# ──────────────────────────────────────────────
# Gate logic for each variant
# ──────────────────────────────────────────────

def gate_a(pool_lu_count, is_crash):
    """Variant A: Current baseline."""
    if is_crash:
        return "RED", 0
    if pool_lu_count == 0:
        return "RED", 0
    elif pool_lu_count <= 2:
        return "AMBER", 1
    else:
        return "GREEN", 2


def gate_b(pool_lu_count, is_crash):
    """Variant B: Looser — only index crash is RED."""
    if is_crash:
        return "RED", 0
    if pool_lu_count == 0:
        return "AMBER", 1
    elif pool_lu_count <= 2:
        return "GREEN", 2
    else:
        return "GREEN", 3


def gate_c(pool_lu_count, market_lu_count, is_crash):
    """Variant C: Hybrid — pool LU + market heat."""
    if is_crash:
        return "RED", 0
    if pool_lu_count >= 3:
        return "GREEN", 2
    elif pool_lu_count >= 1:
        return "AMBER", 1
    elif market_lu_count >= 50:
        return "AMBER", 1
    else:
        return "RED", 0


# ──────────────────────────────────────────────
# Simulation engine
# ──────────────────────────────────────────────

def simulate(variant_name, pool_codes, pool_data, pool_lu_sets,
             crash_dates, market_lu_map, trading_dates,
             gate_fn, extra_gate_args=False):
    """Run the backtest for one gate variant.

    gate_fn: takes (pool_lu_count, is_crash, [market_lu_count]) → (verdict, max_trades)
    """
    positions = []  # list of {code, entry_date, entry_price, no_lu_days}
    trades = []     # list of {code, entry_date, exit_date, entry_price, exit_price, exit_type, pnl}
    skipped_yzb = 0
    day_stats = {"RED": 0, "AMBER": 0, "GREEN": 0}

    for i, today in enumerate(trading_dates):
        # ── Step 1: process exits for open positions ──
        new_positions = []
        for pos in positions:
            code = pos["code"]
            df = pool_data[code]
            # Get yesterday's row (today is the current trading day, we want prev close)
            # Actually, "yesterday" means the trading day before today
            # We need to look at the row before 'today' in the df
            today_idx = df[df["Date"] == today].index
            if len(today_idx) == 0:
                new_positions.append(pos)
                continue
            idx = today_idx[0]
            if idx == 0:
                new_positions.append(pos)
                continue
            yesterday_close = df.loc[idx - 1, "Close"]

            exit_type = None
            exit_price = None

            # Check TP first
            if yesterday_close >= pos["entry_price"] * 1.10:
                exit_type = "TP"
                exit_price = df.loc[idx, "Open"]
            # Check SL
            elif yesterday_close <= pos["entry_price"] * 0.95:
                exit_type = "SL"
                exit_price = df.loc[idx, "Open"]
            # Check time stop: update no_lu_days
            else:
                yesterday_str = str(df.loc[idx - 1, "Date"])
                yesterday_hit_lu = yesterday_str in pool_lu_sets[code]
                if yesterday_hit_lu:
                    pos["no_lu_days"] = 0
                else:
                    pos["no_lu_days"] += 1
                if pos["no_lu_days"] >= 3:
                    exit_type = "TIME"
                    exit_price = df.loc[idx, "Open"]

            if exit_type is not None and not pd.isna(exit_price) and exit_price > 0:
                pnl = (exit_price - pos["entry_price"]) / pos["entry_price"] * POSITION_SIZE
                trades.append({
                    "code": code,
                    "entry_date": pos["entry_date"],
                    "exit_date": today,
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "exit_type": exit_type,
                    "pnl": pnl,
                })
            else:
                new_positions.append(pos)

        positions = new_positions

        # ── Step 2: determine gate for today ──
        # We need YESTERDAY's pool LU count (LU on day before 'today')
        # Find yesterday's trading date
        prev_date = _prev_trading_date(trading_dates, today)
        if prev_date is None:
            continue  # no previous data to detect LU

        pool_lu_yesterday = sum(1 for c in pool_codes if prev_date in pool_lu_sets.get(c, set()))
        is_crash = prev_date in crash_dates  # crash detected on prev_date close → blocks today's trading

        if extra_gate_args:
            market_lu = market_lu_map.get(prev_date, 0)
            verdict, max_trades = gate_fn(pool_lu_yesterday, market_lu, is_crash)
        else:
            verdict, max_trades = gate_fn(pool_lu_yesterday, is_crash)

        day_stats[verdict] += 1

        if max_trades == 0:
            continue

        # ── Step 3: select candidates and enter ──
        # Eligible: pool stocks that hit LU yesterday AND not already held
        held_codes = {p["code"] for p in positions}
        candidates = []
        for code in pool_codes:
            if code in held_codes:
                continue
            if prev_date not in pool_lu_sets.get(code, set()):
                continue
            df = pool_data[code]
            if df is None:
                continue
            # Check 一字板: skip if today's open is already at limit-up
            if is_yiziban(df, today):
                skipped_yzb += 1
                continue
            # Verify today exists
            today_rows = df[df["Date"] == today]
            if today_rows.empty:
                continue
            candidates.append(code)

        # Randomly pick up to max_trades
        if len(candidates) > max_trades:
            candidates = random.sample(candidates, max_trades)

        for code in candidates:
            df = pool_data[code]
            entry_price = df[df["Date"] == today]["Open"].iloc[0]
            if pd.isna(entry_price) or entry_price <= 0:
                continue
            positions.append({
                "code": code,
                "entry_date": today,
                "entry_price": entry_price,
                "no_lu_days": 0,
            })

    # ── Close any remaining positions at last available close ──
    last_date = trading_dates[-1]
    for pos in positions:
        code = pos["code"]
        df = pool_data[code]
        last_rows = df[df["Date"] <= last_date]
        if last_rows.empty:
            continue
        exit_price = float(last_rows["Close"].iloc[-1])
        pnl = (exit_price - pos["entry_price"]) / pos["entry_price"] * POSITION_SIZE
        trades.append({
            "code": code,
            "entry_date": pos["entry_date"],
            "exit_date": last_date,
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "exit_type": "EOD",
            "pnl": pnl,
        })

    return trades, day_stats, skipped_yzb


def _prev_trading_date(all_dates, today):
    """Return the trading day immediately before 'today'."""
    for i, d in enumerate(all_dates):
        if d == today and i > 0:
            return all_dates[i - 1]
    return None


# ──────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────

def report(variant_name, description, trades, day_stats, skipped_yzb):
    print(f"\n=== Variant {variant_name}: {description} ===")
    r, a, g = day_stats.get("RED", 0), day_stats.get("AMBER", 0), day_stats.get("GREEN", 0)
    total_days = r + a + g
    days_traded = a + g
    n_trades = len(trades)
    total_pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = (wins / n_trades * 100) if n_trades > 0 else 0

    # Max drawdown
    if trades:
        cum = 0
        peak = 0
        mdd = 0
        for t in trades:
            cum += t["pnl"]
            peak = max(peak, cum)
            mdd = min(mdd, cum - peak)
        mdd = abs(mdd)
    else:
        mdd = 0

    tp_trades = [t for t in trades if t["exit_type"] == "TP"]
    sl_trades = [t for t in trades if t["exit_type"] == "SL"]
    time_trades = [t for t in trades if t["exit_type"] == "TIME"]
    eod_trades = [t for t in trades if t["exit_type"] == "EOD"]

    print(f"Trading days: {total_days}  |  Days traded: {days_traded}  |  RED/AMBER/GREEN: {r}/{a}/{g}")
    print(f"Total trades: {n_trades}  |  Win rate: {win_rate:.1f}%  |  Total P&L: ¥{total_pnl:+,.0f}")
    print(f"Avg profit per trade: ¥{total_pnl/n_trades:+,.0f}" if n_trades > 0 else "Avg profit per trade: N/A", end="")
    print(f"  |  Max drawdown: ¥{mdd:,.0f}")
    exit_parts = []
    for label, tset in [("TP", tp_trades), ("SL", sl_trades), ("TIME", time_trades), ("EOD", eod_trades)]:
        if tset:
            pnl_sum = sum(t["pnl"] for t in tset)
            exit_parts.append(f"{label}={len(tset)} (¥{pnl_sum:+,.0f})")
    print(f"Exit breakdown: {', '.join(exit_parts)}")
    print(f"Skipped (一字板): {skipped_yzb}")

    return {
        "variant": variant_name,
        "days": total_days,
        "days_traded": days_traded,
        "trades": n_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "mdd": mdd,
        "tp": len(tp_trades),
        "sl": len(sl_trades),
        "time": len(time_trades),
    }


def comparison_table(results):
    print("\n" + "=" * 80)
    print(f"{'Variant':<12} {'Trades':>8} {'Win%':>8} {'Total P&L':>14} {'Max DD':>12} {'Avg/Trade':>12}")
    print("-" * 80)
    for r in results:
        avg = r["total_pnl"] / r["trades"] if r["trades"] > 0 else 0
        print(f"{r['variant']:<12} {r['trades']:>8} {r['win_rate']:>7.1f}% {r['total_pnl']:>+13,.0f} ¥{r['mdd']:>11,.0f} ¥{avg:>+11,.0f}")
    print("=" * 80)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print(" Yao Gate Backtest — 3 Variants Compared")
    print(f" Range: {START_DATE} → {END_DATE}")
    print(f" Position size: ¥{POSITION_SIZE:,}")
    print("=" * 60)

    # 1. Parse pool codes
    pool_codes = parse_yao_codes(YAOLIST_PATH)
    print(f"\nPool: {len(pool_codes)} stocks from yaolist.md")

    # 2. Load pool stock data & pre-compute LU sets
    print("\nLoading pool stock data & detecting limit-ups...")
    pool_data = {}
    pool_lu_sets = {}
    for code in pool_codes:
        path = os.path.join(FLAT_YAO_DIR, f"{code}.csv")
        if not os.path.exists(path):
            print(f"  WARNING: {code}.csv not found, skipping")
            continue
        df = load_csv(path)
        if df is None:
            print(f"  WARNING: {code}.csv empty/unreadable, skipping")
            continue
        # Filter to relevant range + a bit before for prev_close
        df = df[df["Date"] >= "2025-12-01"]
        pool_data[code] = df
        pool_lu_sets[code] = detect_lu(df)

    valid_codes = [c for c in pool_codes if c in pool_data]
    print(f"  Loaded {len(valid_codes)} pool stocks")

    # 3. Build trading date list
    # Use first pool stock's dates as reference
    ref_df = pool_data[valid_codes[0]]
    all_dates = sorted(set(str(d) for d in ref_df["Date"]))
    trading_dates = [d for d in all_dates if START_DATE <= d <= END_DATE]
    print(f"  Trading dates in range: {len(trading_dates)} ({trading_dates[0]} → {trading_dates[-1]})")

    # 4. Index crash detection
    print("\nLoading index crash data...")
    index_map = load_index_cache()
    crash_dates = build_index_crash_set(index_map)
    print(f"  Indices loaded: {list(index_map.keys())}")
    print(f"  Crash dates detected: {len(crash_dates)}")
    if crash_dates:
        print(f"  Crash dates: {sorted(crash_dates)}")

    # 5. Market-wide LU count (for variant C)
    print("\nBuilding market-wide LU map (for Variant C)...")
    market_lu_map = build_market_lu_map(FLAT_YAO_DIR, "2025-12-01", END_DATE)

    # 6. Run simulations (reset seed for reproducible random selection in each variant)
    print("\nRunning simulations...")

    random.seed(RANDOM_SEED)
    trades_a, stats_a, skip_a = simulate(
        "A", valid_codes, pool_data, pool_lu_sets,
        crash_dates, market_lu_map, trading_dates,
        lambda pool_lu, is_crash: gate_a(pool_lu, is_crash),
        extra_gate_args=False,
    )

    random.seed(RANDOM_SEED)
    trades_b, stats_b, skip_b = simulate(
        "B", valid_codes, pool_data, pool_lu_sets,
        crash_dates, market_lu_map, trading_dates,
        lambda pool_lu, is_crash: gate_b(pool_lu, is_crash),
        extra_gate_args=False,
    )

    random.seed(RANDOM_SEED)
    trades_c, stats_c, skip_c = simulate(
        "C", valid_codes, pool_data, pool_lu_sets,
        crash_dates, market_lu_map, trading_dates,
        lambda pool_lu, market_lu, is_crash: gate_c(pool_lu, market_lu, is_crash),
        extra_gate_args=True,
    )

    # 7. Print reports
    r1 = report("A", "Current baseline (0→RED, 1-2→AMBER max1, 3+→GREEN max2, crash→RED)",
                trades_a, stats_a, skip_a)
    r2 = report("B", "Looser (0→AMBER max1, 1-2→GREEN max2, 3+→GREEN max3, crash→RED)",
                trades_b, stats_b, skip_b)
    r3 = report("C", "Hybrid (3+→GREEN max2, 1-2→AMBER max1, 0+mkt≥50→AMBER max1, 0+mkt<50→RED, crash→RED)",
                trades_c, stats_c, skip_c)

    comparison_table([r1, r2, r3])


if __name__ == "__main__":
    main()
