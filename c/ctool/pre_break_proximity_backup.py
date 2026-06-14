#!/usr/bin/env python3
"""
pre_break.py — Pre-breakout scanner (companion to framed.md §3B)

Scans A-share stocks for pre-breakout setups: price consolidating near N-day high
with building volume, BEFORE the limit-up fires. Complements scan_cn.py which only
sees stocks AFTER they've already sealed at +10%.

Precision-first design: fewer picks, higher bar. Fake breakouts penalised harder
than missed opportunities.

Input (all local — zero network calls):
  - tool/stock_history_ak/<code>.csv  — daily OHLCV per stock
  - tool/share_data/stock_meta.csv    — market cap + Shenwan industry
  - tool/all_a_stocks.csv             — symbol→name fallback

Output:
  - Top 4 candidates per day, each with breakout probability (0–100)
  - Saved to share_data/prebreak_<date>.txt

Usage:
    python pre_break.py                        # scan latest trading day
    python pre_break.py --date 20250115        # scan a specific day
    python pre_break.py --range 202501 202506  # scan a range (for backtesting)
    python pre_break.py -q                     # quiet: save file only
"""

import os
import sys
import time
import argparse
import csv
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ── paths ──────────────────────────────────────────────────────────────────
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
META_PATH = os.path.join(TOOL_DIR, "share_data", "stock_meta.csv")
STOCK_LIST_PATH = os.path.join(TOOL_DIR, "all_a_stocks.csv")
SHARE_DIR = os.path.join(TOOL_DIR, "share_data")

# 6test.md tracker (Part 1 written here; Part 2 filled by grade_6test.py)
ROOT_DIR = os.path.dirname(TOOL_DIR)
SIXTEST_PATH = os.path.join(ROOT_DIR, "c", "6test.md")
PART2_MARKER = "## 2 Results"   # both scripts split the file on this line

# ── framed.md thresholds ───────────────────────────────────────────────────
MCAP_MIN = 5_000_000_000      # ¥5B  free-float
MCAP_MAX = 50_000_000_000     # ¥50B free-float
NEAR_HIGH_PCT = 5.0           # close must be within 5% of 20-day high
ABOVE_HIGH_MAX = 3.0         # but no more than 3% ABOVE it (already broke out = too late)
VOL_RATIO_MIN = 1.2           # today's volume / 5-day avg volume
LIMIT_UP_MAIN = 1.099         # main board 10% limit (allow for rounding)
LIMIT_UP_GEM = 1.199          # ChiNext/STAR 20% limit
LOOKBACK_DAYS = 120           # trading days needed before scoring day
MIN_SCORE = 45                # minimum score to appear in output
TOP_N = 4                     # candidates per day

# ── helpers ────────────────────────────────────────────────────────────────

def is_gem(code: str) -> bool:
    """ChiNext (300xxx) or STAR (688xxx) — 20% daily limit."""
    return code.startswith("300") or code.startswith("301") or code.startswith("688")


def load_meta() -> dict:
    """Load stock_meta.csv, filter by cap, return {code: {name, industry, float_mcap}}."""
    meta = {}
    if not os.path.exists(META_PATH):
        print("ERROR: stock_meta.csv not found", file=sys.stderr)
        return meta

    with open(META_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row.get("code", "").strip()
            name = row.get("name", "").strip()
            industry = row.get("industry", "").strip()
            try:
                mcap = float(row.get("float_mcap_now", "0") or "0")
            except ValueError:
                mcap = 0

            # framed.md §2B: free-float cap 5B–50B
            if MCAP_MIN <= mcap <= MCAP_MAX and name and not name.startswith("*ST") and not name.startswith("ST"):
                meta[code] = {"name": name, "industry": industry, "float_mcap": mcap}
    return meta


def load_stock_history(code: str) -> pd.DataFrame | None:
    """Read a per-stock CSV, return DataFrame with Date index, or None."""
    path = os.path.join(HISTORY_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return None

    try:
        df = pd.read_csv(path, parse_dates=["Date"])
        if df.empty or "Close" not in df.columns or "Volume" not in df.columns:
            return None
        df = df.set_index("Date").sort_index()
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df["High"] = pd.to_numeric(df.get("High", df["Close"]), errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
        df = df.dropna(subset=["Close", "Volume"])
        return df
    except Exception:
        return None


def compute_score(pct_from_high: float, vol_ratio: float,
                  ma_aligned: bool, sector_cluster_count: int,
                  has_recent_limit_up: bool) -> tuple[int, dict]:
    """
    Score a single stock-day on 0–100 scale.
    Returns (total_score, breakdown_dict).
    """
    score = 0
    bd = {}

    # 1. Distance from 20-day high — closer to (but not above) high = more imminent (max 30 pts)
    #    Right at high (-1% to +0.5%): 30,  within 2% below: 22,  within 3.5%: 14,  within 5%: 7
    #    Just broke through (0.5% to 3% above): 10 (still actionable but less ideal)
    if -1.0 <= pct_from_high <= 0.5:
        bd["proximity"] = 30
    elif pct_from_high >= -2.0:
        bd["proximity"] = 22
    elif pct_from_high >= -3.5:
        bd["proximity"] = 14
    elif pct_from_high >= -NEAR_HIGH_PCT:
        bd["proximity"] = 7
    else:  # above high (0.5% to 3%)
        bd["proximity"] = 10
    score += bd["proximity"]

    # 2. Volume expansion (max 25 pts)
    if vol_ratio >= 2.0:
        bd["volume"] = 25
    elif vol_ratio >= 1.5:
        bd["volume"] = 20
    elif vol_ratio >= 1.3:
        bd["volume"] = 12
    elif vol_ratio >= VOL_RATIO_MIN:
        bd["volume"] = 6
    else:
        bd["volume"] = 0
    score += bd["volume"]

    # 3. MA alignment: 5d > 10d > 20d (max 15 pts)
    if ma_aligned:
        bd["ma_align"] = 15
    else:
        bd["ma_align"] = 0
    score += bd["ma_align"]

    # 4. Sector clustering — how many peers also near breakout? (max 15 pts)
    if sector_cluster_count >= 4:
        bd["sector"] = 15
    elif sector_cluster_count >= 3:
        bd["sector"] = 12
    elif sector_cluster_count >= 2:
        bd["sector"] = 8
    else:
        bd["sector"] = 0
    score += bd["sector"]

    # 5. Recent limit-up proves capital interest (max 15 pts)
    if has_recent_limit_up:
        bd["recent_zt"] = 15
    else:
        bd["recent_zt"] = 0
    score += bd["recent_zt"]

    return min(score, 100), bd


def is_trading_day(d: pd.Timestamp, df_index) -> bool:
    """Check if date d exists in the DataFrame index (i.e., was a trading day)."""
    return d in df_index


# ── main scan logic ────────────────────────────────────────────────────────

def scan_day(target_date: str, meta: dict, verbose: bool = True) -> list[dict]:
    """
    Scan all qualifying stocks for a single target_date.
    Returns list of candidate dicts sorted by score desc.
    """
    target_dt = pd.Timestamp(target_date)

    # we need LOOKBACK_DAYS of history before target_date
    lookback_start = target_dt - pd.Timedelta(days=LOOKBACK_DAYS * 2)

    raw_signals = []  # per-stock signals for sector clustering pass

    total = len(meta)
    processed = 0
    t0 = time.time()

    for code, info in meta.items():
        processed += 1
        if verbose and processed % 500 == 0:
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            print(f"  ... {processed}/{total} stocks ({rate:.0f}/s)", file=sys.stderr)

        df = load_stock_history(code)
        if df is None or df.empty:
            continue

        # filter to relevant date range
        df = df[(df.index >= lookback_start) & (df.index <= target_dt)]
        if len(df) < LOOKBACK_DAYS:
            continue

        # check if target_date is in the data
        if target_dt not in df.index:
            continue

        # get today's row
        today = df.loc[target_dt]
        close = today["Close"]
        volume = today["Volume"]
        if pd.isna(close) or pd.isna(volume) or close <= 0 or volume <= 0:
            continue

        # compute 20-day high (excluding today)
        window_20 = df["High"].iloc[-21:-1]  # 20 days before today
        if len(window_20) < 20:
            continue
        high_20 = window_20.max()

        # pct from 20-day high (negative = below high)
        pct_from_high = (close - high_20) / high_20 * 100

        # volume ratio
        vol_5d_avg = df["Volume"].iloc[-6:-1].mean()
        if vol_5d_avg <= 0:
            continue
        vol_ratio = volume / vol_5d_avg

        # MA alignment: 5d > 10d > 20d
        ma_5 = df["Close"].iloc[-5:].mean()
        ma_10 = df["Close"].iloc[-10:].mean()
        ma_20 = df["Close"].iloc[-20:].mean()
        ma_aligned = (ma_5 > ma_10 > ma_20)

        # limit-up check: today NOT already limit-up
        if len(df) >= 2:
            prev_close = df["Close"].iloc[-2]
            limit = LIMIT_UP_GEM if is_gem(code) else LIMIT_UP_MAIN
            if close >= prev_close * limit:
                continue  # already limit-up today, skip

        # recent limit-up in last 20 days (excluding today)
        has_recent_zt = False
        if len(df) >= 22:
            for i in range(-21, -1):
                prev_c = df["Close"].iloc[i]
                if i > -len(df):
                    prev_pc = df["Close"].iloc[i - 1]
                    limit = LIMIT_UP_GEM if is_gem(code) else LIMIT_UP_MAIN
                    if prev_c >= prev_pc * limit * 0.999:  # 0.999 to handle rounding
                        has_recent_zt = True
                        break

        # pre-filter: must be within 5% BELOW to 3% ABOVE 20d high, and volume >= 1.2x
        if pct_from_high < -NEAR_HIGH_PCT or pct_from_high > ABOVE_HIGH_MAX:
            continue
        if vol_ratio < VOL_RATIO_MIN:
            continue

        raw_signals.append({
            "code": code,
            "name": info["name"],
            "industry": info["industry"],
            "close": close,
            "pct_from_high": pct_from_high,
            "vol_ratio": vol_ratio,
            "ma_aligned": ma_aligned,
            "has_recent_zt": has_recent_zt,
            "high_20": high_20,
            "ma_5": ma_5,
            "volume": volume,
            "vol_5d_avg": vol_5d_avg,
        })

    if verbose:
        elapsed = time.time() - t0
        print(f"  Done: {processed} stocks, {len(raw_signals)} pre-filtered ({elapsed:.1f}s)",
              file=sys.stderr)

    # ── sector clustering pass ──────────────────────────────────────────
    # Count how many raw_signals per industry (≥ 2 = sector momentum)
    industry_counts = defaultdict(int)
    for s in raw_signals:
        industry_counts[s["industry"]] += 1

    # ── score and rank ──────────────────────────────────────────────────
    candidates = []
    for s in raw_signals:
        score, bd = compute_score(
            pct_from_high=s["pct_from_high"],
            vol_ratio=s["vol_ratio"],
            ma_aligned=s["ma_aligned"],
            sector_cluster_count=industry_counts.get(s["industry"], 0),
            has_recent_limit_up=s["has_recent_zt"],
        )
        if score < MIN_SCORE:
            continue
        s["score"] = score
        s["score_bd"] = bd
        candidates.append(s)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:TOP_N]


def format_output(candidates: list[dict], date_str: str) -> str:
    """Format candidates as a readable report."""
    lines = []
    lines.append(f"pre_break candidates for {date_str}")
    lines.append("=" * 60)
    if not candidates:
        lines.append("  No candidates above minimum score threshold.")
        return "\n".join(lines)

    for i, c in enumerate(candidates, 1):
        bd = c["score_bd"]
        lines.append(f"\n#{i}  {c['code']}  {c['name']}  [{c['industry']}]")
        lines.append(f"    Probability: {c['score']}/100")
        lines.append(f"    Close: {c['close']:.2f}  |  20d High: {c['high_20']:.2f}  "
                     f"({c['pct_from_high']:+.1f}%)")
        lines.append(f"    Volume: {c['volume']:.0f}  |  5d Avg: {c['vol_5d_avg']:.0f}  "
                     f"(×{c['vol_ratio']:.1f})")
        lines.append(f"    MA 5d: {c['ma_5']:.2f}  {'aligned' if c['ma_aligned'] else 'not aligned'}")
        lines.append(f"    Score: proximity={bd['proximity']}  volume={bd['volume']}  "
                     f"MA={bd['ma_align']}  sector={bd['sector']}  recent_zt={bd['recent_zt']}")
    return "\n".join(lines)


def get_latest_trading_day() -> str:
    """Find the latest date available in the history data."""
    # sample a few stock files to find the latest common date
    latest = None
    meta = load_meta()
    for code in list(meta.keys())[:20]:
        path = os.path.join(HISTORY_DIR, f"{code}.csv")
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, parse_dates=["Date"])
                if not df.empty:
                    d = pd.Timestamp(df["Date"].max())
                    if latest is None or d > latest:
                        latest = d
            except Exception:
                continue
    if latest is None:
        return datetime.now().strftime("%Y-%m-%d")
    return latest.strftime("%Y-%m-%d")


def write_6test_names(date_str: str, candidates: list[dict]) -> None:
    """Write Part 1 (Names) of c/6test.md and reset Part 2 to a placeholder.

    Only Part 1 is authored here. The grader (grade_6test.py) reads these names
    back and fills Part 2 with forward returns, overwriting Part 2 only.
    Pick lines are whitespace-delimited (code name score); A-share names contain
    no spaces, so the grader can split them back cleanly.
    """
    lines = [
        "# 6test — pre_break pick tracker",
        "",
        "## 1 Names",
        "",
        f"Scan date: {date_str}",
        "Entry basis: buy next trading day open, exit close of 10th trading day",
        "",
        f"{'code':<8}{'name':<16}score",
    ]
    for c in candidates:
        lines.append(f"{c['code']:<8}{c['name']:<16}{c['score']}")
    lines += [
        "",
        PART2_MARKER,
        "",
        "(not yet graded — run: python tool/grade_6test.py)",
        "",
    ]
    os.makedirs(os.path.dirname(SIXTEST_PATH), exist_ok=True)
    with open(SIXTEST_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pre-breakout scanner for A-shares (framed.md §3B companion)"
    )
    parser.add_argument("--date", help="Scan a single date (YYYY-MM-DD)")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"),
                        help="Scan a range of dates (YYYYMM or YYYYMMDD)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Save file only, no stdout")
    parser.add_argument("--out", default="prebreak",
                        help="Output basename (saved to share_data/<out>_<date>.txt)")
    args = parser.parse_args()

    verbose = not args.quiet

    # load metadata once
    if verbose:
        print("Loading stock metadata...", file=sys.stderr)
    meta = load_meta()
    if verbose:
        print(f"  {len(meta)} stocks in 5B–50B range ({MCAP_MIN/1e9:.0f}B–{MCAP_MAX/1e9:.0f}B)",
              file=sys.stderr)

    # determine date(s) to scan
    dates = []
    if args.range:
        # range mode: parse start/end as YYYYMM or YYYYMMDD
        start_s, end_s = args.range
        start_dt = pd.Timestamp(start_s[:4] + "-" + start_s[4:6] + "-" +
                                (start_s[6:8] if len(start_s) >= 8 else "01"))
        end_dt = pd.Timestamp(end_s[:4] + "-" + end_s[4:6] + "-" +
                              (end_s[6:8] if len(end_s) >= 8 else
                               str(pd.Timestamp(end_s[:4] + "-" + end_s[4:6] + "-01").days_in_month)))
        # generate trading days — just use any stock's index as reference
        d = start_dt
        while d <= end_dt:
            dates.append(d.strftime("%Y-%m-%d"))
            d += pd.Timedelta(days=1)
        if verbose:
            print(f"Scan range: {dates[0]} → {dates[-1]} ({len(dates)} calendar days)",
                  file=sys.stderr)
    elif args.date:
        dates = [args.date]
    else:
        dates = [get_latest_trading_day()]
        if verbose:
            print(f"Using latest trading day: {dates[0]}", file=sys.stderr)

    # scan
    for date_str in dates:
        if verbose and len(dates) > 1:
            print(f"\n--- {date_str} ---", file=sys.stderr)
        elif verbose:
            print(f"Scanning {date_str}...", file=sys.stderr)

        candidates = scan_day(date_str, meta, verbose=verbose)
        report = format_output(candidates, date_str)

        # save to file
        os.makedirs(SHARE_DIR, exist_ok=True)
        out_path = os.path.join(SHARE_DIR, f"{args.out}_{date_str}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")

        # single-date scan -> also refresh Part 1 (Names) of the 6test tracker
        if len(dates) == 1:
            write_6test_names(date_str, candidates)
            if verbose:
                print(f"Wrote Part 1 (Names) -> {SIXTEST_PATH}", file=sys.stderr)

        if verbose:
            if not args.quiet:
                print(report)
            print(f"Saved: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
