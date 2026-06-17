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
SIXTEST_PATH = os.path.join(ROOT_DIR, "6test.md")
PART2_MARKER = "## 2 Results"   # both scripts split the file on this line

# ── gates (rebuilt from winner_study.py: momentum near 52-week highs) ────────
# The old "consolidation near the 20-day high" thesis was confirmed DEAD out-of-sample
# (0.9–1.1x lift in every period). What actually precedes a +30%/10d move is momentum
# near long-term highs; these thresholds come from the high-lift bins of that study.
MOM20_MIN = 13.0              # 20-day momentum % (top-lift bin: ~2.3-2.8x)
MOM5_MIN = 5.0               # 5-day momentum % (recent thrust)
HIGH250_NEAR = 9.0            # close must be within 9% BELOW the 250-day (52wk) high
VOL_RATIO20_MIN = 1.4         # today's volume / prior-20d avg (volume confirmation)
TURNOVER_MIN_YI = 2.0         # min daily turnover in 100M yuan (liquidity/buyability)
MIN_HIST_FOR_250 = 200        # need >=200 days before the 250d-high gate is meaningful
LIMIT_UP_MAIN = 1.099         # main board 10% limit (allow for rounding)
LIMIT_UP_GEM = 1.199          # ChiNext/STAR 20% limit
LOOKBACK_DAYS = 120           # trading days needed before scoring day
TOP_N = 4                     # candidates per day

# ── helpers ────────────────────────────────────────────────────────────────

def is_gem(code: str) -> bool:
    """ChiNext (300xxx) or STAR (688xxx) — 20% daily limit."""
    return code.startswith("300") or code.startswith("301") or code.startswith("688")


def load_meta() -> dict:
    """Load stock_meta.csv, return {code: {name, industry, float_mcap}}.

    The old 5B-50B cap band was dropped: market cap was a weak predictor in winner_study,
    and a turnover gate (in scan_day) handles liquidity/buyability better. We only exclude
    ST / *ST names here.
    """
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

            if name and not name.startswith("*ST") and not name.startswith("ST"):
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


def is_trading_day(d: pd.Timestamp, df_index) -> bool:
    """Check if date d exists in the DataFrame index (i.e., was a trading day)."""
    return d in df_index


# ── main scan logic ────────────────────────────────────────────────────────

def scan_day(target_date: str, meta: dict, verbose: bool = True) -> list[dict]:
    """
    Scan all qualifying stocks for a single target_date with the momentum gates.
    Returns up to TOP_N candidate dicts, ranked by capped mom20 score desc.
    """
    target_dt = pd.Timestamp(target_date)
    lookback_start = target_dt - pd.Timedelta(days=LOOKBACK_DAYS * 3)  # wide enough for 250d high

    candidates = []
    total = len(meta)
    processed = 0
    t0 = time.time()

    for code, info in meta.items():
        processed += 1
        if verbose and processed % 500 == 0:
            elapsed = time.time() - t0
            print(f"  ... {processed}/{total} stocks ({processed / elapsed:.0f}/s)", file=sys.stderr)

        df = load_stock_history(code)
        if df is None or df.empty:
            continue
        df = df[(df.index >= lookback_start) & (df.index <= target_dt)]
        if len(df) < LOOKBACK_DAYS or target_dt not in df.index:
            continue

        close = df["Close"].iloc[-1]
        volume = df["Volume"].iloc[-1]
        if pd.isna(close) or pd.isna(volume) or close <= 0 or volume <= 0:
            continue

        c = df["Close"]
        # momentum gates
        mom20 = (close / c.iloc[-21] - 1) * 100
        mom5 = (close / c.iloc[-6] - 1) * 100
        if mom20 < MOM20_MIN or mom5 < MOM5_MIN:
            continue

        # near the 52-week (250d) high — only gate when we have enough history
        high_250 = df["High"].iloc[-250:].max()
        pct_from_high250 = (close - high_250) / high_250 * 100
        have_250 = len(df) >= MIN_HIST_FOR_250
        if have_250 and pct_from_high250 < -HIGH250_NEAR:
            continue

        # trend confirmation: above 20d MA and 5>10>20 aligned
        ma_5 = c.iloc[-5:].mean()
        ma_10 = c.iloc[-10:].mean()
        ma_20 = c.iloc[-20:].mean()
        if not (close > ma_20 and ma_5 > ma_10 > ma_20):
            continue

        # volume confirmation vs prior 20d
        vol20_prior = df["Volume"].iloc[-21:-1].mean()
        if vol20_prior <= 0 or volume / vol20_prior < VOL_RATIO20_MIN:
            continue
        vol_ratio20 = volume / vol20_prior

        # liquidity / buyability: min daily turnover (100M yuan)
        turnover_yi = volume * 100 * close / 1e8
        if turnover_yi < TURNOVER_MIN_YI:
            continue

        # not already limit-up today (can't buy a sealed board)
        prev_close = c.iloc[-2]
        limit = LIMIT_UP_GEM if is_gem(code) else LIMIT_UP_MAIN
        if close >= prev_close * limit:
            continue

        # Capped score: mom20 > 60 gets no extra rank credit (worst returns observed at ≥80)
        cap_score = min(mom20, 60.0)
        candidates.append({
            "code": code,
            "name": info["name"],
            "industry": info["industry"],
            "close": close,
            "score": round(cap_score, 1),     # rank key; capped at 60 (sweet spot 45-59 per backtest)
            "mom20": mom20,
            "mom5": mom5,
            "pct_from_high250": pct_from_high250 if have_250 else None,
            "vol_ratio20": vol_ratio20,
            "turnover_yi": turnover_yi,
            "ma_aligned": True,
        })

    if verbose:
        print(f"  Done: {processed} stocks, {len(candidates)} passed gates "
              f"({time.time() - t0:.1f}s)", file=sys.stderr)

    # Primary: capped score. Secondary: vol_ratio20 (volume confirmation, 1.4x lift per winner_study)
    candidates.sort(key=lambda x: (x["score"], x["vol_ratio20"]), reverse=True)
    return candidates[:TOP_N]


def format_output(candidates: list[dict], date_str: str) -> str:
    """Format candidates as a readable report."""
    lines = [f"pre_break (momentum) candidates for {date_str}", "=" * 60]
    if not candidates:
        lines.append("  No candidates passed the gates.")
        return "\n".join(lines)

    for i, c in enumerate(candidates, 1):
        h250 = "n/a" if c["pct_from_high250"] is None else f"{c['pct_from_high250']:+.1f}%"
        lines.append(f"\n#{i}  {c['code']}  {c['name']}  [{c['industry']}]")
        lines.append(f"    Close: {c['close']:.2f}  |  mom20: {c['mom20']:+.1f}%  mom5: {c['mom5']:+.1f}%")
        lines.append(f"    Dist from 52wk high: {h250}  |  vol×20d: {c['vol_ratio20']:.1f}  "
                     f"|  turnover: {c['turnover_yi']:.1f} yi")
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
        print(f"  {len(meta)} non-ST stocks loaded (cap band dropped; turnover gate handles liquidity)",
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
