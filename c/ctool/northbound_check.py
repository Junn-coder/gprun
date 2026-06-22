#!/usr/bin/env python3
"""
Northbound capital-flow checker — companion to hota.md dimension A.

Answers: "is northbound buying or selling this stock?" with 3d/5d/10d summaries.
Works on individual stocks, your watchlist, or the full hot_a_stocks universe.

Data source: akshare → stock_hsgt_individual_em (EastMoney 沪深港通个股流向)
Anti-block: on-disk cache per stock (share_data/nb_<CODE>.csv), 5s inter-call sleep.

Usage:
    # Single stock — northbound only
    python northbound_check.py 000158 002607

    # Northbound + Sina LHB institution flow (dual-source)
    python northbound_check.py 000158 --lhb

    # From watchlist (codes extracted from c/chold.md + watchlistd.md)
    python northbound_check.py --watchlist --lhb

    # Scan full hot_a_stocks universe for sustained outflow flags
    python northbound_check.py --scan --min-outflow 2000 --lhb

Dependencies: akshare, pandas (same venv as cn_stock.py / scan_cn.py)
"""

import os
import sys
import time
import argparse
import csv
import json
from datetime import datetime, timedelta
from collections import defaultdict

import akshare as ak
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "share_data")
STOCK_LIST = os.path.join(HERE, "hot_a_stocks.csv")
LHB_CACHE = os.path.join(CACHE_DIR, "lhb_sina_latest.csv")
os.makedirs(CACHE_DIR, exist_ok=True)


def load_symbols_from_csv(path, limit=None):
    """Read hot_a_stocks.csv → list of (code, name)."""
    syms = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            code = row.get("symbol", "").strip()
            name = row.get("name", "").strip()
            if code:
                syms.append((code, name))
            if limit and len(syms) >= limit:
                break
    return syms


def extract_codes_from_md(filepath):
    """Extract 6-digit stock codes from a markdown file (chold.md)."""
    import re
    codes = set()
    try:
        with open(filepath, encoding="utf-8") as f:
            text = f.read()
        for m in re.finditer(r"\b(00\d{4}|30\d{4}|60\d{4}|68\d{4})\b", text):
            codes.add(m.group(1))
    except FileNotFoundError:
        pass
    return sorted(codes)


def cache_path(code):
    return os.path.join(CACHE_DIR, f"nb_{code}.csv")


def fetch_northbound(code):
    """Fetch northbound flow history for one stock. Returns DataFrame or None."""
    path = cache_path(code)
    df = None

    # Try cache first
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, parse_dates=["date"])
            if len(df) > 0:
                last_date = df["date"].max()
                # Refresh if stale (>1 trading day old)
                if pd.Timestamp.now() - last_date < timedelta(days=2):
                    return df
        except Exception:
            pass

    # Network fetch with retry
    for attempt in range(3):
        try:
            raw = ak.stock_hsgt_individual_em(symbol=code)
            break
        except Exception as e:
            if attempt < 2:
                wait = 3 * (attempt + 1)
                print(f"  retry {attempt+1}/2 in {wait}s...", end=" ", flush=True,
                      file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"  ⚠ {code}: fetch failed after 3 attempts ({e})", file=sys.stderr)
                raw = None

    if raw is None or raw.empty:
        if df is not None:
            return df  # return stale cache
        return None

    # Normalize columns
    raw = raw.rename(columns={
        "持股日期": "date",
        "当日收盘价": "close",
        "当日涨跌幅": "chg_pct",
        "持股数量": "shares_held",
        "持股市值": "mv_held",
        "持股数量占A股百分比": "held_pct_a",
        "今日增持股数": "shares_added",
        "今日增持资金": "capital_added",
        "今日持股市值变化": "mv_change",
    })
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.sort_values("date")

    # Save cache
    raw.to_csv(path, index=False)
    return raw


def compute_trend(df, windows=(3, 5, 10)):
    """Compute net flow trend over windows. Returns dict of {f'{n}d_net': float, ...}."""
    if df is None or len(df) == 0:
        return {}
    result = {}
    for w in windows:
        if len(df) >= w:
            net = df["capital_added"].tail(w).sum()
            result[f"{w}d_net"] = net
            result[f"{w}d_pos"] = int((df["capital_added"].tail(w) > 0).sum())
            result[f"{w}d_neg"] = int((df["capital_added"].tail(w) < 0).sum())
    # 1d
    result["1d_net"] = df["capital_added"].iloc[-1] if len(df) else 0
    result["last_date"] = str(df["date"].iloc[-1].date()) if len(df) else ""
    result["held_pct"] = df["held_pct_a"].iloc[-1] if len(df) else 0
    return result


def flag_summary(trend):
    """Human-readable flag from trend dict."""
    if not trend:
        return "no data"
    flags = []
    for w in (3, 5, 10):
        key = f"{w}d_net"
        if key in trend:
            val = trend[key]
            pos = trend.get(f"{w}d_pos", 0)
            neg = trend.get(f"{w}d_neg", 0)
            if pos >= w:
                flags.append(f"{w}d↑")
            elif neg >= w:
                flags.append(f"{w}d↓")
    if not flags:
        last = trend.get("1d_net", 0)
        if last > 0:
            flags.append("today↑")
        elif last < 0:
            flags.append("today↓")
    return " ".join(flags) if flags else "mixed"


# ---------------------------------------------------------------------------
# Sina LHB (龙虎榜) — institution capital flow, fast single-request
# ---------------------------------------------------------------------------

def fetch_lhb_sina(force=False):
    """Fetch latest LHB institution detail from Sina. Cached for the day.
    Returns DataFrame with columns: code, name, date, inst_buy, inst_sell.
    """
    if not force and os.path.exists(LHB_CACHE):
        try:
            df = pd.read_csv(LHB_CACHE)
            last_date = df["date"].iloc[0] if len(df) else ""
            if str(pd.Timestamp.now().date()) <= str(last_date):
                return df
        except Exception:
            pass

    print("  fetching Sina LHB ...", end=" ", flush=True, file=sys.stderr)
    try:
        raw = ak.stock_lhb_jgmx_sina()
    except Exception as e:
        print(f"failed ({e})", file=sys.stderr)
        return pd.DataFrame(columns=["code", "name", "date", "inst_buy", "inst_sell", "type"])

    if raw is None or raw.empty:
        print("empty", file=sys.stderr)
        return pd.DataFrame(columns=["code", "name", "date", "inst_buy", "inst_sell", "type"])

    df = raw.rename(columns={
        "股票代码": "code",
        "股票名称": "name",
        "交易日期": "date",
        "机构席位买入额": "inst_buy",
        "机构席位卖出额": "inst_sell",
        "类型": "lhb_type",
    })
    df["inst_buy"] = pd.to_numeric(df["inst_buy"], errors="coerce").fillna(0)
    df["inst_sell"] = pd.to_numeric(df["inst_sell"], errors="coerce").fillna(0)
    df["inst_net"] = df["inst_buy"] - df["inst_sell"]
    # date format varies — normalize
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.to_csv(LHB_CACHE, index=False)
    print(f"{len(df)} entries", file=sys.stderr)
    return df


def check_lhb(code, lhb_df):
    """Check if a stock appears in LHB data. Returns dict or None."""
    if lhb_df is None or lhb_df.empty:
        return None
    row = lhb_df[lhb_df["code"] == code]
    if row.empty:
        return None
    r = row.iloc[0]
    buy = r.get("inst_buy", 0) or 0
    sell = r.get("inst_sell", 0) or 0
    net = buy - sell
    if net > 0:
        signal = "LHB-inst↑"
    elif net < 0:
        signal = "LHB-inst↓"
    else:
        signal = "LHB-inst~"
    return {"signal": signal, "inst_buy": buy, "inst_sell": sell, "inst_net": net}

def cmd_stocks(codes, out_json=False, sleep_sec=5, with_lhb=False):
    """Check northbound flow for explicit stock codes."""
    lhb_df = fetch_lhb_sina() if with_lhb else None

    results = {}
    for i, code in enumerate(codes):
        print(f"[{i+1}/{len(codes)}] {code} ...", end=" ", flush=True)
        df = fetch_northbound(code)
        trend = compute_trend(df)
        flag = flag_summary(trend)

        lhb_info = ""
        if lhb_df is not None:
            lhb = check_lhb(code, lhb_df)
            if lhb:
                lhb_info = f"  {lhb['signal']} ¥{lhb['inst_net']:+,.0f}"
            else:
                lhb_info = "  no LHB"

        print(f"{flag}   held {trend.get('held_pct',0):.2f}%  "
              f"3d net ¥{trend.get('3d_net',0):+,.0f}  "
              f"5d net ¥{trend.get('5d_net',0):+,.0f}{lhb_info}")
        results[code] = trend
        if i < len(codes) - 1:
            time.sleep(sleep_sec)

    if out_json:
        print(json.dumps(results, ensure_ascii=False, default=str, indent=2))


def cmd_watchlist(out_json=False, sleep_sec=5, with_lhb=False):
    """Check northbound for stocks in chold.md."""
    codes = set()
    path = os.path.join(os.path.dirname(HERE), "chold.md")
    codes.update(extract_codes_from_md(path))
    if not codes:
        print("No stock codes found in chold.md")
        return
    print(f"Watchlist: {len(codes)} stocks ({', '.join(sorted(codes))})\n")
    cmd_stocks(sorted(codes), out_json=out_json, sleep_sec=sleep_sec, with_lhb=with_lhb)


def cmd_scan(min_outflow=0, limit=None, sleep_sec=5, with_lhb=False):
    """Scan the full hot_a_stocks universe for northbound flags.

    Prints only stocks with sustained outflow (or all if min_outflow=0).
    """
    lhb_df = fetch_lhb_sina() if with_lhb else None

    syms = load_symbols_from_csv(STOCK_LIST, limit=limit)
    print(f"Scanning northbound flow for {len(syms)} stocks "
          f"(hot_a_stocks.csv) ...\n")
    hdr = f"{'code':<7} {'name':<8} {'held%':>6} {'1d':>10} {'3d':>10} " \
          f"{'5d':>10} {'10d':>10}  flag"
    if with_lhb:
        hdr += "        lhb"
    print(hdr)

    flagged = []
    lhb_hits = []
    for i, (code, name) in enumerate(syms):
        df = fetch_northbound(code)
        trend = compute_trend(df)
        flag = flag_summary(trend)

        lhb_str = ""
        if lhb_df is not None:
            lhb = check_lhb(code, lhb_df)
            if lhb:
                lhb_str = f"  {lhb['signal']} ¥{lhb['inst_net']:+,.0f}"
                lhb_hits.append((code, name, lhb))

        outflow_5d = abs(trend.get("5d_net", 0)) if trend.get("5d_net", 0) < 0 else 0
        outflow_3d = abs(trend.get("3d_net", 0)) if trend.get("3d_net", 0) < 0 else 0

        if min_outflow > 0 and outflow_5d < min_outflow and outflow_3d < min_outflow:
            # Skip if no significant outflow
            pass
        else:
            print(f"{code:<7} {name:<8} {trend.get('held_pct',0)*100:>5.2f}% "
                  f"¥{trend.get('1d_net',0):>+9,.0f} ¥{trend.get('3d_net',0):>+9,.0f} "
                  f"¥{trend.get('5d_net',0):>+9,.0f} ¥{trend.get('10d_net',0):>+9,.0f}  "
                  f"{flag}{lhb_str}")
            flagged.append((code, name, trend, flag))

        if (i + 1) % 20 == 0:
            print(f"\n  ... {i+1}/{len(syms)} done\n")

        if i < len(syms) - 1:
            time.sleep(sleep_sec)

    # Summary
    inflow_codes = []
    outflow_codes = []
    for code, name, trend, flag in flagged:
        if "↓" in flag:
            outflow_codes.append(code)
        elif "↑" in flag:
            inflow_codes.append(code)

    print(f"\n--- Scan complete ---")
    print(f"  Sustained inflow:  {len(inflow_codes)} stocks  ({', '.join(inflow_codes) if inflow_codes else 'none'})")
    print(f"  Sustained outflow: {len(outflow_codes)} stocks  ({', '.join(outflow_codes) if outflow_codes else 'none'})")
    if lhb_hits:
        print(f"\n  LHB institution activity (Sina):")
        for code, name, lhb in lhb_hits:
            print(f"    {code} {name}  {lhb['signal']} ¥{lhb['inst_net']:+,.0f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Northbound capital-flow checker (hota.md dimension A)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python northbound_check.py 000158 002607
  python northbound_check.py --watchlist
  python northbound_check.py --scan
  python northbound_check.py --scan --min-outflow 5000
  python northbound_check.py 000158 --json
        """,
    )
    sub = parser.add_mutually_exclusive_group()
    sub.add_argument("--watchlist", action="store_true",
                     help="Check stocks from chold.md")
    sub.add_argument("--scan", action="store_true",
                     help="Scan full hot_a_stocks.csv universe")
    parser.add_argument("codes", nargs="*", help="Stock code(s), e.g. 000158 002607")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--lhb", action="store_true",
                        help="Also check Sina LHB (龙虎榜) institution flow for today")
    parser.add_argument("--min-outflow", type=float, default=0,
                        help="With --scan: only show stocks with 5d outflow >= this amount (¥)")
    parser.add_argument("--limit", type=int, default=0,
                        help="With --scan: limit to first N stocks (for testing)")
    parser.add_argument("--sleep", type=float, default=5,
                        help=f"Inter-call sleep in seconds (default 5)")
    args = parser.parse_args()

    if args.watchlist:
        cmd_watchlist(out_json=args.json, sleep_sec=args.sleep, with_lhb=args.lhb)
    elif args.scan:
        cmd_scan(min_outflow=args.min_outflow, limit=args.limit or None,
                 sleep_sec=args.sleep, with_lhb=args.lhb)
    elif args.codes:
        cmd_stocks(args.codes, out_json=args.json, sleep_sec=args.sleep, with_lhb=args.lhb)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
