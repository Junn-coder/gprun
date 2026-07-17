#!/usr/bin/env python3
"""
Yao-stock pool gate (companion to c/yao/yprompt.md)

Different from index.py (the swing-system gate): this gate is tuned for demon
stocks — it prioritises pool-internal momentum over broad-market index analysis.
Demon stocks often rally independently of the index; shutting them down on a
routine 2% index drop (as index.py does) misses the point.

Gate logic:
  1. Pool limit-up count (yesterday): how many of the yao-pool stocks hit
     limit-up?  Source: akshare stock_zt_pool_em, filtered to the pool.
  2. Index extreme check: only care about a true crash (>3% on any major index).
     A 2% dip is normal noise for demon stocks — we ignore it.
  3. Output: GREEN / AMBER / RED, written to share_data/yao_gate_<date>.txt

Pool codes are read from c/yao/yaolist.md (pool table).

Dependencies: akshare, pandas (same as index.py / cn_stock.py)

Usage:
    python yao_gate.py                        # today, writes report
    python yao_gate.py --date 20260714        # specific trading day
    python yao_gate.py -q                     # quiet: file only, no stdout
"""

import os
import sys
import time
import argparse
import re
from datetime import datetime, timedelta
from io import StringIO

import akshare as ak
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
YAOLIST_PATH = os.path.join(ROOT, "yao", "yaolist.md")
DEFAULT_OUTDIR = os.path.join(HERE, "share_data")

# Indices for the extreme-crash check only (index.py already handles the full
# swing-system gate; we only watch for tail events).
INDEXES = [
    ("000001", "上证综指", "sh000001"),
    ("399006", "创业板指", "sz399006"),
    ("000300", "沪深300",   "sh000300"),
]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def is_block(e):
    s = f"{type(e).__name__} {e}".lower()
    return ("remotedisconnected" in s or "connection aborted" in s
            or "connectionerror" in s or "max retries" in s)


def with_retry(fn, label="", wait=15):
    for attempt in range(2):
        try:
            return fn()
        except Exception as e:
            if is_block(e) and attempt == 0:
                print(f"  ! {label} refused ({type(e).__name__}); backing off {wait}s for ONE retry")
                time.sleep(wait)
                continue
            raise


# ------------------------------------------------------------------
# Parse yaolist.md → 25 codes
# ------------------------------------------------------------------
def parse_yao_codes(path):
    """Extract the 6-digit stock codes from the yaolist.md table."""
    if not os.path.exists(path):
        print(f"ERROR: yaolist.md not found at {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # Match | N | XXXXXX | ...  rows in the pool table
    codes = re.findall(r"\|\s*\d+\s*\|\s*(\d{6})\s*\|", text)
    if not codes:
        print("ERROR: no stock codes found in yaolist.md pool table")
        sys.exit(1)
    return codes  # pool size is controlled by build_yao_pool.py


# ------------------------------------------------------------------
# Pool limit-up count (yesterday)
# ------------------------------------------------------------------
def pool_limitup_count(codes, date_str):
    """Return (count, list_of_codes) of pool stocks in yesterday's limit-up pool."""
    try:
        df = with_retry(
            lambda: ak.stock_zt_pool_em(date=date_str),
            label=f"limit-up pool {date_str}"
        )
    except Exception as e:
        print(f"  x limit-up pool fetch failed: {type(e).__name__}: {e}")
        return 0, []

    if df is None or df.empty:
        return 0, []

    code_col = None
    for col in ["代码", "code", "股票代码"]:
        if col in df.columns:
            code_col = col
            break
    if code_col is None:
        return 0, []

    pool_set = set(df[code_col].astype(str).str.zfill(6))
    hits = [c for c in codes if c in pool_set]
    return len(hits), hits


# ------------------------------------------------------------------
# Index extreme check (crash detection only)
# ------------------------------------------------------------------
def index_extreme_check(date_str):
    """Return list of (name, chg_pct) for indices that dropped >3%."""
    alerts = []
    end = datetime.strptime(date_str, "%Y%m%d")
    start = (end - timedelta(days=10)).strftime("%Y%m%d")

    for code, name, symbol in INDEXES:
        try:
            df = with_retry(
                lambda: ak.stock_zh_index_daily(symbol=symbol),
                label=f"index {code}"
            )
        except Exception as e:
            print(f"  x index {code} fetch failed: {type(e).__name__}: {e}")
            continue

        if df is None or df.empty:
            continue

        # Normalise columns
        ren = {"date": "Date", "close": "Close", "日期": "Date", "收盘": "Close"}
        df = df.rename(columns=ren)
        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        df = df.sort_values("Date")

        target = end.strftime("%Y-%m-%d")
        rows = df[df["Date"] == target]
        if rows.empty:
            continue

        close_today = float(rows["Close"].iloc[0])
        prev_rows = df[df["Date"] < target]
        if prev_rows.empty:
            continue
        close_yesterday = float(prev_rows["Close"].iloc[-1])
        chg = (close_today / close_yesterday - 1) * 100

        if chg < -3:
            alerts.append((name, round(chg, 2)))

    return alerts


# ------------------------------------------------------------------
# Render
# ------------------------------------------------------------------
def render(date_str, pool_codes, lu_count, lu_codes, crash_alerts):
    L = []
    P = L.append
    P("=" * 60)
    P(f" Yao-stock pool gate  date {date_str}")
    P("=" * 60)
    P("Source: akshare stock_zt_pool_em (pool limit-up count)")
    P("       + index daily (extreme-crash check, >3% only)")
    P("")
    P(f"[Pool]  {lu_count} / {len(pool_codes)} stocks hit limit-up")
    if lu_codes:
        P(f"  codes: {', '.join(lu_codes)}")
    else:
        P("  codes: — none —")
    P("")

    if crash_alerts:
        P("[Index extreme]  CRASH DETECTED:")
        for name, chg in crash_alerts:
            P(f"  {name}: {chg:+.2f}%  (>3% drop)")
    else:
        P("[Index extreme]  no crash (>3%) on any major index")
    P("")

    # --- Gate decision (Variant B: looser, backtest-verified) ---
    if crash_alerts:
        gate = "RED"
        why = "index crash >3% — demon stocks don't trade into a cliff"
    elif lu_count >= 3:
        gate = "GREEN"
        why = f"pool hot: {lu_count} limit-ups — hunt aggressively, pick up to 3"
    elif lu_count >= 1:
        gate = "GREEN"
        why = f"pool warm: {lu_count} limit-up(s) — trade, max 2 picks"
    else:  # 0 LU
        gate = "AMBER"
        why = "pool quiet: 0 limit-ups — trade light, max 1 pick"

    P(f"[Verdict]  {gate}")
    P(f"  {why}")
    P("")
    P(f"GREEN → pool active. Scan all {len(pool_codes)}, pick up to 3 (3+ LU) or 2 (1-2 LU) candidates.")
    P("AMBER → pool quiet (0 LU). Max 1 candidate. Market may still have heat elsewhere.")
    P("RED   → 空仓. Index crash >3%. Wait for next scan.")
    return "\n".join(L)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Yao-stock pool gate (demon-stock specific)")
    ap.add_argument("--date", default=None, help="trading day YYYYMMDD (default: today)")
    ap.add_argument("--pool", default=YAOLIST_PATH, help="path to yaolist.md")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress stdout")
    args = ap.parse_args()

    date_str = args.date or datetime.now().strftime("%Y%m%d")

    pool_codes = parse_yao_codes(args.pool)

    # 1. Pool limit-up count
    lu_count, lu_codes = pool_limitup_count(pool_codes, date_str)

    # 2. Index extreme check
    crash_alerts = index_extreme_check(date_str)

    # 3. Render & save
    report = render(date_str, pool_codes, lu_count, lu_codes, crash_alerts)
    if not args.quiet:
        print(report)

    os.makedirs(args.outdir, exist_ok=True)
    path = os.path.join(args.outdir, f"yao_gate_{date_str}.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(report + "\n")
    if not args.quiet:
        print(f"OK: yao gate report saved to {path}")


if __name__ == "__main__":
    main()
