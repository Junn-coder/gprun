#!/usr/bin/env python3
"""
Bulk-refresh US-stock daily bars — the US counterpart of downa.py.

downa.py bulk-downloads A-shares into stock_history_ak/<symbol>.csv.
downu.py bulk-refreshes US tickers into u/ushare_data/price_<TICKER>.txt, in the
EXACT same format us_stock.py writes (so the watchlist's data source stays uniform).

It reuses us_stock.py's proven, stable internals (stock_us_daily via 新浪财经 — no
stock_us_spot_em, which is what used to RemoteDisconnect). No reinvented fetch logic.

Two modes:
  * No tickers given  -> refresh EVERY existing price_*.txt in the out dir
                         (preserves each file's original filename + display name).
                         This is the one-shot "un-stale the whole watchlist" path.
  * Tickers given     -> refresh/create just those (e.g. add AMD LRCX WDC).

Incremental by construction: us_stock.read_existing() merges + de-dups by Date, so
re-runs only append new rows.

Usage:
    python downu.py                       # refresh all existing price_*.txt in u/ushare_data
    python downu.py NVDA AMD AVGO         # refresh/create just these
    python downu.py --outdir some/dir     # point elsewhere
    python downu.py --commit              # git add/commit/push after
    python downu.py --start 2024-01-01 --end 2026-05-31
"""

import os
import re
import sys
import time
import argparse
import subprocess
from io import StringIO
from datetime import datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Force UTF-8 stdout/stderr (Windows cp1252 safety); no-op on Linux.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Reuse the stable US fetch/format code rather than duplicate it.
from us_stock import (
    fetch_full, with_retry, read_existing, resolve_name,
    SOURCE_LABEL, DEFAULT_LOOKBACK_DAYS, git_commit,
)

# The watchlist's actual US data lives here (NOT tool/share_data).
DEFAULT_OUTDIR = os.path.normpath(os.path.join(HERE, "..", "ushare_data"))

# Fallback display names for tickers us_stock.NAMES doesn't know, used when a file's
# header carries no proper name (e.g. old "====" banner headers, or first refresh).
NAME_OVERRIDES = {
    "GEV": "GE Vernova", "VRT": "Vertiv Holdings", "LITE": "Lumentum Holdings",
    "LRCX": "Lam Research", "WDC": "Western Digital", "SNDK": "SanDisk",
    "PLTR": "Palantir Technologies", "INTC": "Intel",
    "ANET": "Arista Networks", "AVAV": "AeroVironment", "KTOS": "Kratos Defense",
    "ETN": "Eaton", "VST": "Vistra",
}


def good_name(token):
    t = token.upper()
    n = resolve_name(t)
    return n if n != t else NAME_OVERRIDES.get(t, t)


def existing_files(outdir):
    """List (ticker_token, display_name, path) for every price_*.txt in outdir.

    ticker_token  -> from the filename (case preserved so we rewrite the same file)
    display_name  -> the file's "<Name> (<TOKEN>) - Daily ..." header line; we scan the
                     first few lines so a leading "====" banner doesn't hide it. Falls
                     back to NAME_OVERRIDES / the ticker when the header has no real name.
    """
    out = []
    if not os.path.isdir(outdir):
        return out
    for fn in sorted(os.listdir(outdir)):
        m = re.match(r"^price_(.+)\.txt$", fn)
        if not m:
            continue
        token = m.group(1)
        path = os.path.join(outdir, fn)
        name = good_name(token)
        try:
            with open(path, encoding="utf-8") as f:
                head = [f.readline() for _ in range(4)]
            for ln in head:
                hm = re.match(r"^(.*?\S)\s*\([A-Za-z.\-]+\)\s*-\s*Daily", ln.strip())
                if hm and hm.group(1).upper() != token.upper():  # a real name, not the bare ticker
                    name = hm.group(1)
                    break
        except Exception:
            pass
        out.append((token, name, path))
    return out


def save_to_path(ticker, name, path, start, end):
    """Fetch full history, slice to [start,end], merge into `path`, return (total, added).

    Mirrors us_stock.save_history but writes to an EXPLICIT path so existing filenames
    (e.g. lowercase price_intel.txt) are preserved instead of forced to price_<UPPER>.txt.
    """
    full = with_retry(lambda: fetch_full(ticker), label=ticker)
    if full is None or full.empty:
        return 0, 0
    full = full[(full["Date"] >= start) & (full["Date"] <= end)]
    if full.empty:
        return 0, 0

    old, last_date = read_existing(path)
    if old is not None and last_date:
        merged = pd.concat([old.astype({"Date": str}), full]).drop_duplicates(subset="Date", keep="last")
        merged = merged.sort_values("Date").reset_index(drop=True)
        added = len(merged) - len(old)
    else:
        merged, added = full.reset_index(drop=True), len(full)

    header = (
        f"{name} ({ticker.upper()}) - Daily Historical Prices\n"
        f"Source: {SOURCE_LABEL}\n"
        f"Range: {merged['Date'].iloc[0]} to {merged['Date'].iloc[-1]}\n"
        f"Total trading days: {len(merged)}\n\n"
    )
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        merged.to_csv(f, index=False, lineterminator="\n")
    return len(merged), added


def build_targets(tickers, outdir):
    """Return list of (fetch_ticker, display_name, out_path)."""
    if tickers:
        return [(t.strip().upper(), good_name(t.strip().upper()),
                 os.path.join(outdir, f"price_{t.strip().upper()}.txt"))
                for t in tickers]
    files = existing_files(outdir)
    # fetch with UPPER token (akshare expects e.g. INTC/NVDA), write back to original path
    return [(token.upper(), name, path) for token, name, path in files]


def main():
    ap = argparse.ArgumentParser(description="Bulk-refresh US daily bars into price_<TICKER>.txt (US counterpart of downa.py)")
    ap.add_argument("tickers", nargs="*", help="US tickers, e.g. NVDA AMD. Omit to refresh all existing price_*.txt")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR, help=f"default: {DEFAULT_OUTDIR}")
    ap.add_argument("--start", default=None, help="start YYYY-MM-DD (default ~2y back)")
    ap.add_argument("--end", default=None, help="end YYYY-MM-DD (default today)")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between tickers (default 2)")
    ap.add_argument("--commit", action="store_true", help="git add/commit/push after")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.strptime(end, "%Y-%m-%d") -
                           timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    targets = build_targets(args.tickers, args.outdir)
    if not targets:
        print(f"No tickers given and no price_*.txt found in {args.outdir}. Nothing to do.")
        print("Tip: seed it first, e.g.  python downu.py NVDA AMD AVGO")
        return

    mode = "given tickers" if args.tickers else f"all {len(targets)} existing files"
    print(f"Refreshing {mode}  ({start} .. {end})  ->  {args.outdir}")

    ok = added_total = 0
    for i, (ticker, name, path) in enumerate(targets):
        if i > 0:
            time.sleep(args.sleep)  # polite pacing
        try:
            total, added = save_to_path(ticker, name, path, start, end)
            if total == 0:
                print(f"  x {ticker:<6s} {name:<20s}  no data")
                continue
            print(f"  ✓ {ticker:<6s} {name:<20s}  {total} rows (+{added})  ->  {os.path.basename(path)}")
            ok += 1
            added_total += added
        except Exception as e:
            print(f"  x {ticker:<6s} failed: {type(e).__name__}: {e}")

    print(f"\nDone: {ok}/{len(targets)} ok, +{added_total} new rows total.")

    if args.commit:
        git_commit(args.outdir)


if __name__ == "__main__":
    main()
