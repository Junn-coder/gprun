#!/usr/bin/env python3
"""
A-share market-regime tool (companion to framed.md §1 / hota.md)

Goal: answer framed.md Layer 1 BEFORE looking at any single stock —
      "is the market a green / amber / red light today?"

  §1A  Index regime  : SSE Composite + ChiNext (+ CSI300) vs 5/10-day MA & slope.
  §1B  Sentiment temp: limit-up count / max board height / broken-board count,
                       summarized from the latest scan_zt_<date>.csv that
                       scan_cn.py already wrote  => ZERO extra network calls.

* Anti-block design (same discipline as cn_stock.py — must NOT get blacklisted) *
  - An index is a SINGLE series: one light call each (NOT the full-market spot
    table that broke the old scripts). Sina source (stock_zh_index_daily).
  - Incremental cache: index_<code>.txt already exists -> only fetch the few
    missing days and merge. First run pulls ~2 years once.
  - Circuit-breaker: on refusal, one long pause + one retry, then give up.
  - §1B reads scan_cn.py's cached CSV — no network. The FULL failed-board rate
    (needs the limit-down + broken-board pools, which ARE heavy full-market
    tables) is opt-in via --breadth, OFF by default.

Data source: akshare — Sina for the index series (stock_zh_index_daily).

Dependencies:
    pip install -r requirements.txt   (akshare, pandas)

Usage:
    python index.py                       # today's regime; prints AND auto-saves index_<date>.txt
    python index.py --date 20260527       # a specific trading day (for §1B CSV match)
    python index.py --breadth             # also pull limit-down + broken-board pools (heavier)
    python index.py -q                     # quiet: save file only, no stdout
    python index.py --no-cache             # force fresh index fetch
"""

import os
import sys
import time
import argparse
from io import StringIO
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

# Force UTF-8 stdout/stderr so diagnostic glyphs (↪ ✓ ✗ ⚠) never crash the
# Windows cp1252 console. On Linux stdout is already UTF-8, so this is a no-op.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTDIR = os.path.join(HERE, "share_data")
DEFAULT_LOOKBACK_DAYS = 730
COLUMNS = ["Date", "Open", "Close", "High", "Low", "Volume", "Turnover"]

# (code, display name, akshare symbol). framed.md §1A watches SSE + ChiNext;
# CSI300 added as the institutional-breadth read.
INDEXES = [
    ("000001", "上证综指", "sh000001"),
    ("399006", "创业板指", "sz399006"),
    ("000300", "沪深300", "sh000300"),
]


# ------------------------------------------------------------------
# Fetch (Sina only — EastMoney's *_em index endpoint removed; it always refused)
# ------------------------------------------------------------------
def is_block(e):
    s = f"{type(e).__name__} {e}".lower()
    return ("remotedisconnected" in s or "connection aborted" in s
            or "connectionerror" in s or "max retries" in s)


def with_retry(fn, label="", wait=15):
    """Circuit-breaker: on a block, ONE long pause + ONE retry, then give up."""
    for attempt in range(2):
        try:
            return fn()
        except Exception as e:
            if is_block(e) and attempt == 0:
                print(f"  ! {label} refused ({type(e).__name__}); backing off {wait}s for ONE retry (no hammering)")
                time.sleep(wait)
                continue
            raise


def _norm(df):
    """Normalize EastMoney / Sina index frames to COLUMNS."""
    ren = {
        "date": "Date", "open": "Open", "close": "Close", "high": "High",
        "low": "Low", "volume": "Volume", "amount": "Turnover",
        "日期": "Date", "开盘": "Open", "收盘": "Close", "最高": "High",
        "最低": "Low", "成交量": "Volume", "成交额": "Turnover",
    }
    df = df.rename(columns=ren)
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    if "Turnover" not in df.columns:
        df["Turnover"] = ""
    return df[[c for c in COLUMNS if c in df.columns]].reset_index(drop=True)


def fetch_index(symbol, start, end):
    """Sina index daily (stock_zh_index_daily) is the only source. EastMoney's
    *_em index endpoint was removed — it reliably refused connections."""
    df = _norm(ak.stock_zh_index_daily(symbol=symbol))
    return df[(df["Date"] >= start) & (df["Date"] <= end)].reset_index(drop=True)


# ------------------------------------------------------------------
# Incremental cache (same scheme as cn_stock.py price files)
# ------------------------------------------------------------------
def read_existing(path):
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        hdr = next(i for i, ln in enumerate(lines) if ln.startswith("Date,"))
        df = pd.read_csv(StringIO("".join(lines[hdr:])))
        df["Date"] = df["Date"].astype(str)
        return df, str(df["Date"].iloc[-1])
    except Exception:
        return None, None


def load_index(code, name, symbol, outdir, end, use_cache):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"index_{code}.txt")
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    old, last_date = read_existing(path)
    if use_cache and old is not None and last_date == end:
        return old                                   # already current through end
    if old is not None and last_date:
        fetch_start = (datetime.strptime(last_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
        new = with_retry(lambda: fetch_index(symbol, fetch_start, end), label=f"{name} {code}")
        if new is None or new.empty:
            return old
        merged = pd.concat([old, new]).drop_duplicates(subset="Date", keep="last")
        merged = merged.sort_values("Date").reset_index(drop=True)
    else:
        merged = with_retry(lambda: fetch_index(symbol, start, end), label=f"{name} {code}")
        if merged is None or merged.empty:
            return None

    header = (
        f"{name} ({code}) - Daily Index History\n"
        f"Source: akshare stock_zh_index_daily (新浪)\n"
        f"Range: {merged['Date'].iloc[0]} to {merged['Date'].iloc[-1]}\n"
        f"Total trading days: {len(merged)}\n\n"
    )
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        merged.to_csv(f, index=False, lineterminator="\n")
    return merged


# ------------------------------------------------------------------
# §1A: per-index traffic light (framed.md §1A)
# ------------------------------------------------------------------
def classify_index(df):
    """Return dict with MAs, slopes and a framed.md §1A light: GREEN/AMBER/RED."""
    d = df.copy()
    d["Close"] = pd.to_numeric(d["Close"], errors="coerce")
    d["Volume"] = pd.to_numeric(d["Volume"], errors="coerce")
    if len(d) < 12:
        return None
    close = d["Close"].to_numpy()
    ma5 = d["Close"].rolling(5).mean().to_numpy()
    ma10 = d["Close"].rolling(10).mean().to_numpy()
    vol = d["Volume"].to_numpy()

    last = close[-1]
    chg = (close[-1] / close[-2] - 1) * 100
    above5, above10 = last > ma5[-1], last > ma10[-1]
    slope5 = ma5[-1] > ma5[-3]                       # sloping up over recent sessions
    slope10 = ma10[-1] > ma10[-3]
    two_down = close[-1] < close[-2] < close[-3]
    vol_shrink = vol[-1] < vol[-2] < vol[-3]

    if chg < -2 or (two_down and vol_shrink):
        light, why = "RED", "freeze: >2% drop or consecutive down on shrinking volume -> cash, wait for a wrap-up reversal"
    elif not above10:
        light, why = "AMBER", "broke the 10-day MA -> ebb, trade reduced (Phase A: AMBER ≈ 80% of GREEN)"
    elif above5 and above10 and slope5 and slope10:
        light, why = "GREEN", "above both 5/10-day MA, both sloping up -> OK to trade"
    else:
        light, why = "AMBER", "mixed (off 5-day MA or flat slope) -> trade light, strongest names only"

    return dict(date=d["Date"].iloc[-1], close=last, chg=chg, ma5=ma5[-1], ma10=ma10[-1],
                above5=above5, above10=above10, slope5=slope5, slope10=slope10,
                light=light, why=why)


# ------------------------------------------------------------------
# §1B: sentiment temperature (from scan_cn.py's cached CSV; --breadth = heavy pools)
# ------------------------------------------------------------------
def read_scan_csv(date, outdir):
    path = os.path.join(outdir, f"scan_zt_{date}.csv")
    if not os.path.exists(path):
        return None, path
    try:
        df = pd.read_csv(path, dtype={"代码": str})
        return df, path
    except Exception:
        return None, path


def sentiment_from_scan(df):
    zt = len(df)
    max_board = pd.to_numeric(df.get("连板数"), errors="coerce").max()
    broke = pd.to_numeric(df.get("炸板次数"), errors="coerce")
    sealed_with_breaks = int((broke > 0).sum()) if broke is not None else 0
    return dict(zt=zt, max_board=int(max_board) if pd.notna(max_board) else 0,
                sealed_with_breaks=sealed_with_breaks)


def breadth_pools(date, sleep):
    """OPT-IN: limit-down + broken-board pools (heavy full-market tables)."""
    out = {}
    dt = with_retry(lambda: ak.stock_zt_pool_dtgc_em(date=date), label=f"limit-down pool {date}")
    time.sleep(sleep)
    zb = with_retry(lambda: ak.stock_zt_pool_zbgc_em(date=date), label=f"broken-board pool {date}")
    out["limit_down"] = 0 if dt is None else len(dt)
    out["broken_board"] = 0 if zb is None else len(zb)
    return out


# ------------------------------------------------------------------
# Render
# ------------------------------------------------------------------
def overall_light(per_index):
    lights = [c["light"] for c in per_index.values() if c]
    if "RED" in lights:
        return "RED" if lights.count("RED") >= 2 or per_index.get("000001", {}).get("light") == "RED" else "AMBER"
    if all(l == "GREEN" for l in lights):
        return "GREEN"
    return "AMBER"


def render(date, per_index, senti, breadth):
    L = []
    P = L.append
    P("=" * 70)
    P(f" A-share market regime (framed.md §1)  date {date}")
    P("=" * 70)
    P("Note: this is the Layer-1 gate, NOT a buy call. Red = cash; Amber = light,")
    P("      strongest only; Green = offensive (framed.md §10).")
    P("")
    P("[§1A Index regime]")
    P(f"  {'index':<14s}{'close':>9s}{'chg%':>7s}{'5MA':>9s}{'10MA':>9s}  light  read")
    for code, name, _ in INDEXES:
        c = per_index.get(code)
        if not c:
            P(f"  {name+' '+code:<14s}{'(no data)':>9s}")
            continue
        P(f"  {name+' '+code:<14s}{c['close']:>9.1f}{c['chg']:>+7.2f}{c['ma5']:>9.1f}{c['ma10']:>9.1f}"
          f"  {c['light']:<5s}  {c['why']}")
    P("")
    P("[§1B Sentiment temperature]")
    if senti:
        P(f"  limit-ups: {senti['zt']}   max consecutive board: {senti['max_board']}   "
          f"sealed names that broke intraday: {senti['sealed_with_breaks']}")
        warm = ("warming up" if senti["zt"] >= 60 and senti["max_board"] >= 5 else
                "freeze/cold" if senti["zt"] < 30 else "neutral / mixed")
        P(f"  rough read: {warm}  (framed.md §1B: warm = ZT rising + 5-board leaders + failed-board <30%)")
    else:
        P("  (no scan_zt CSV for this date — run scan_cn.py first to populate §1B)")
    if breadth:
        total_attempt = (senti['zt'] if senti else 0) + breadth['broken_board']
        fbr = (breadth['broken_board'] / total_attempt * 100) if total_attempt else 0
        P(f"  --breadth: limit-downs: {breadth['limit_down']}   broken-board (failed seals): {breadth['broken_board']}"
          f"   failed-board rate ~ {fbr:.0f}%")
        P(f"             (framed.md §1B: <30% warm/trade, >40% ebb/stop; limit-downs > limit-ups = freeze)")
    P("")
    P(f"[Verdict]  {overall_light(per_index)}")
    P("  GREEN -> framed.md §10 offensive: take exposure to the §5 warm-up cap, hit Layer-3 triggers.")
    P("  AMBER -> trade light, strongest main-line only, tighter size.")
    P("  RED   -> hold the red light: cash / <=10-30% exposure, no new entries.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="A-share market-regime / Layer-1 gate (anti-block)")
    ap.add_argument("--date", default=None, help="trading day YYYYMMDD (defaults to today); used to match the scan_zt CSV")
    ap.add_argument("--breadth", action="store_true", help="also pull limit-down + broken-board pools (heavier full-market tables)")
    ap.add_argument("--sleep", type=float, default=5.0, help="seconds between heavy --breadth calls (default 5)")
    ap.add_argument("--no-cache", action="store_true", help="force fresh index fetch")
    ap.add_argument("--out", default="index", help="basename of saved regime report (share_data/<out>_<date>.txt); empty to disable")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress stdout (file save still happens)")
    args = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    date_compact = args.date or datetime.now().strftime("%Y%m%d")

    per_index = {}
    for code, name, symbol in INDEXES:
        try:
            df = load_index(code, name, symbol, args.outdir, today, not args.no_cache)
            per_index[code] = classify_index(df) if df is not None else None
        except Exception as e:
            print(f"  x {name} {code} failed: {type(e).__name__}: {e}")
            per_index[code] = None
        time.sleep(1)

    if not any(per_index.values()):
        print("\nERROR: no index data could be fetched (refused or errored). Stopped per anti-block rule.")
        print("       Suggestion: run later, or use --no-cache off, or switch network.")
        sys.exit(1)

    scan_df, scan_path = read_scan_csv(date_compact, args.outdir)
    senti = sentiment_from_scan(scan_df) if scan_df is not None else None

    breadth = None
    if args.breadth:
        try:
            breadth = breadth_pools(date_compact, args.sleep)
        except Exception as e:
            print(f"  x --breadth pools failed: {type(e).__name__}: {e}")

    report = render(date_compact, per_index, senti, breadth)
    if not args.quiet:
        print(report)

    if args.out:
        path = os.path.join(args.outdir, f"{args.out}_{date_compact}.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(report + "\n")
        if not args.quiet:
            print(f"\nOK: regime report saved to {path}")


if __name__ == "__main__":
    main()
