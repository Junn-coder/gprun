#!/usr/bin/env python3
"""
A-share short-term candidate scanner (companion to framed.md / hota.md)

Goal: resolve the "chicken-and-egg" problem — automatically produce a raw candidate
      list of "hot sectors + leaders" from the whole market, so a human (or AI) can
      then converge it down to 6 names per framed.md §2/§3 and drop them into
      watchlistd.md.

* Anti-block design (important — this machine must NOT get blacklisted by EastMoney) *
  - Default: only ONE network request — the limit-up pool (stock_zt_pool_em). It
    already carries industry / consecutive-board count / first-seal time / sealing
    capital, which is enough to compute hot sectors and leaders.
  - Circuit-breaker backoff: on refusal (RemoteDisconnected / ConnectionError),
    take ONE long pause and ONE retry. Still fails -> give up; never hammer.
    Hammering is what gets you blacklisted.
  - On-disk cache: each day's limit-up pool is saved to
    share_data/scan_zt_<date>.csv, so repeated analysis does not hit the network.
  - Default inter-call sleep is 5s, tunable via --sleep. Slow is fine; getting
    blocked is not.

Data source: akshare — EastMoney limit-up pool (stock_zt_pool_em). NOTE: this
dataset is EastMoney-exclusive; Sina / Tencent do not expose a 涨停板 (limit-up)
pool, so unlike index.py / cn_stock.py this tool CANNOT be switched to Sina.

Dependencies:
    pip install -r requirements.txt   (akshare, pandas)

Usage:
    python scan_cn.py                       # scan today; prints report AND auto-saves to share_data/candidates_<date>.txt
    python scan_cn.py --final 8             # converge to 8 names instead of 6
    python scan_cn.py --date 20260526       # specify trading day
    python scan_cn.py --sleep 8             # slower, more stable
    python scan_cn.py --no-cache            # force a fresh network fetch
    python scan_cn.py --out ""              # disable auto-save (stdout only)
    python scan_cn.py --out picks           # change basename -> share_data/picks_<date>.txt

Output (in this order):
  1. Final shortlist of N (default 6) — #1 leader from each top hot sector,
     diversified across themes. This is the take-away.
  2. Hot sectors ranked by limit-up count.
  3. Full leader breakdown per hot sector — for cross-checking.
The full report is auto-saved to share_data/candidates_<date>.txt by default.
Next step: feed the final shortlist into cn_stock.py --history to pull
history, validate entries, then fill c/watchlistd.md.
"""

import os
import sys
import time
import argparse
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

# Force UTF-8 stdout/stderr so diagnostic glyphs and any Chinese in error text
# never crash the Windows cp1252 console. On Linux this is already UTF-8 (no-op).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTDIR = os.path.join(HERE, "share_data")

# framed.md §2: free-float market cap "friendly" band (CNY) — 2026-06-13: floor 50e8→30e8
CAP_LOW, CAP_HIGH = 30e8, 500e8
# framed.md §2C / hota §11: consecutive-board count >= this = high-position relay
# risk, flag as warning
HIGH_BOARD_RISK = 5

# akshare stock_zt_pool_em returns Chinese column names — rename to English so
# the rest of the code reads cleanly. Keep this map in one place.
COLUMN_MAP = {
    "代码": "code",
    "名称": "name",
    "涨跌幅": "pct_change",
    "最新价": "price",
    "成交额": "turnover",
    "流通市值": "float_mcap",
    "总市值": "total_mcap",
    "换手率": "turnover_rate",
    "封板资金": "seal_capital",
    "首次封板时间": "first_seal_time",
    "最后封板时间": "last_seal_time",
    "炸板次数": "broken_board_count",
    "涨停统计": "limit_up_stats",
    "连板数": "board_count",
    "所属行业": "industry",
}


def is_block(e):
    s = f"{type(e).__name__} {e}".lower()
    return ("remotedisconnected" in s or "connection aborted" in s
            or "connectionerror" in s or "max retries" in s)


def safe_fetch(label, fn, sleep_after, wait=15):
    """Circuit-breaker fetch: on refusal, one long pause + one retry; give up after.
    Returns df or None."""
    for attempt in range(2):                      # at most 1 retry
        try:
            df = fn()
            time.sleep(sleep_after)               # pause after success too — don't burst
            return df
        except Exception as e:
            if is_block(e) and attempt == 0:
                print(f"  ! {label} refused ({type(e).__name__}); backing off {wait}s for ONE retry (no hammering)")
                time.sleep(wait)
                continue
            print(f"  x {label} giving up: {type(e).__name__}: {e}")
            return None
    return None


# ------------------------------------------------------------------
# Limit-up pool (the only required network request)
# ------------------------------------------------------------------
def load_zt_pool(date, outdir, sleep, use_cache):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"scan_zt_{date}.csv")
    if use_cache and os.path.exists(path):
        print(f"  v cache hit {path} (no network)")
        df = pd.read_csv(path, dtype={"代码": str})
    else:
        df = safe_fetch(f"limit-up pool {date}", lambda: ak.stock_zt_pool_em(date=date), sleep)
        if df is not None and not df.empty:
            df.to_csv(path, index=False, encoding="utf-8")
    if df is not None and not df.empty:
        df = df.rename(columns=COLUMN_MAP)
    return df


def resolve_trading_date(arg_date, outdir, sleep, use_cache):
    """If --date is given, use it. Otherwise walk back from today to the first
    trading day with data (max 4 days back)."""
    if arg_date:
        return arg_date, load_zt_pool(arg_date, outdir, sleep, use_cache)

    d = datetime.now()
    for back in range(5):
        date = (d - timedelta(days=back)).strftime("%Y%m%d")
        df = load_zt_pool(date, outdir, sleep, use_cache)
        if df is None:
            return date, None                      # refused/errored: stop, do not keep probing other dates
        if not df.empty:
            if back > 0:
                print(f"  i {datetime.now():%Y%m%d} has no data; falling back to most recent trading day {date}")
            return date, df
    return date, df


# ------------------------------------------------------------------
# Analysis: sector ranking + leader picking
# ------------------------------------------------------------------
def to_num(s):
    return pd.to_numeric(s, errors="coerce")


def analyze(df, min_zt, top_sectors):
    df = df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    for c in ["board_count", "broken_board_count", "turnover_rate",
              "float_mcap", "turnover", "seal_capital"]:
        if c in df.columns:
            df[c] = to_num(df[c])

    # Sector ranking: primarily by limit-up count, tiebreak on max board count
    g = df.groupby("industry").agg(
        limit_up_count=("code", "count"),
        max_board=("board_count", "max"),
        total_turnover=("turnover", "sum"),
    ).reset_index()
    g = g.sort_values(["limit_up_count", "max_board"], ascending=False).reset_index(drop=True)

    hot = g[g["limit_up_count"] >= min_zt].head(top_sectors)
    return df, g, hot


def pick_leaders(df_sector):
    """Leader ranking within a sector: higher board -> earlier first seal ->
    fewer broken boards -> larger sealing capital."""
    d = df_sector.sort_values(
        by=["board_count", "first_seal_time", "broken_board_count", "seal_capital"],
        ascending=[False, True, True, False],
    )
    return d


def cap_flag(mv):
    if pd.isna(mv):
        return "cap?  "
    if CAP_LOW <= mv <= CAP_HIGH:
        return "cap-OK"      # framed.md §2 friendly band
    return "cap-NG"          # too small (easily ramped) or too large (can't move)


def board_flag(lb):
    return "!HIGH" if (not pd.isna(lb) and lb >= HIGH_BOARD_RISK) else ""


def stage_hint(zt_count, max_lb):
    """Rough mapping to hota.md §1 stage — heuristic only."""
    if zt_count >= 10:
        return "mainstream (main-up?)"
    if zt_count >= 5:
        return "anomaly (launch?)"
    return "incubation?"


# ------------------------------------------------------------------
# Output
# ------------------------------------------------------------------
def build_final_shortlist(hot, df, n):
    """Pick the #1 leader from each of the top hot sectors, up to n names —
    naturally diversifies across themes (framed.md / hota.md principle: don't
    concentrate in one theme). If hot sectors < n, take the next-best leader
    from the strongest sectors to fill."""
    picks = []
    seen = set()
    # First pass: top leader from each top sector
    for sector in hot["industry"].tolist():
        sd = pick_leaders(df[df["industry"] == sector])
        for _, r in sd.iterrows():
            if r["code"] not in seen:
                picks.append((sector, r))
                seen.add(r["code"])
                break
        if len(picks) >= n:
            break
    # Second pass: if we still don't have n, take #2, #3... from strongest sectors
    if len(picks) < n:
        for sector in hot["industry"].tolist():
            if len(picks) >= n:
                break
            sd = pick_leaders(df[df["industry"] == sector])
            for _, r in sd.iterrows():
                if r["code"] in seen:
                    continue
                picks.append((sector, r))
                seen.add(r["code"])
                if len(picks) >= n:
                    break
    return picks[:n]


def render(date, df, sector_rank, hot, leaders_per_sector, final_picks):
    lines = []
    P = lines.append
    P("=" * 78)
    P(f" A-share short-term candidate scan  date {date}  limit-ups {len(df)}  hot sectors {len(hot)}")
    P("=" * 78)
    P("Note: this is a RAW candidate list, NOT a buy recommendation. You still need to")
    P("      apply framed.md §1 sentiment gate / §2 selection / §3 entry, then validate")
    P("      entries / stops against historical data.")
    P("")
    P(f"[Final shortlist — top {len(final_picks)} (1 leader per hot sector, diversified)]")
    P(f"  {'#':>2s}  {'code':<7s}{'name':<9s}{'sector':<14s}{'bd':>3s}{'first':>7s}{'brk':>4s}"
      f"{'turn%':>7s}{'float(100M)':>12s}  flags")
    for i, (sector, r) in enumerate(final_picks, 1):
        t = str(r.get("first_seal_time", "")).zfill(6)
        t = f"{t[:2]}:{t[2:4]}" if len(t) == 6 else t
        mv = r.get("float_mcap")
        P(f"  {i:>2d}  {r['code']:<7s}{str(r['name']):<9s}{sector:<14s}"
          f"{int(r['board_count']):>3d}{t:>7s}{int(r['broken_board_count']):>4d}"
          f"{r['turnover_rate']:>7.1f}{(mv/1e8 if not pd.isna(mv) else 0):>12.1f}"
          f"  {cap_flag(mv)} {board_flag(r['board_count'])}")
    P("")
    P("[Hot sectors ranked by limit-up count]")
    P(f"  {'sector':<14s}{'#ZT':>4s}{'maxBoard':>10s}{'turnover(100M)':>16s}  stage hint")
    for _, r in hot.iterrows():
        P(f"  {r['industry']:<14s}{int(r['limit_up_count']):>4d}{int(r['max_board']):>10d}"
          f"{r['total_turnover']/1e8:>16.1f}  {stage_hint(r['limit_up_count'], r['max_board'])}")
    P("")
    P("[Full leader breakdown per hot sector]  (cap-OK = framed.md §2 friendly 30-500亿 CNY; !HIGH = board>=5, careful relay)")
    for sector, d in leaders_per_sector:
        P(f"\n  * {sector}")
        P(f"    {'code':<7s}{'name':<9s}{'bd':>3s}{'first':>7s}{'brk':>4s}"
          f"{'turn%':>7s}{'float(100M)':>12s}  flags")
        for _, r in d.iterrows():
            t = str(r.get("first_seal_time", "")).zfill(6)
            t = f"{t[:2]}:{t[2:4]}" if len(t) == 6 else t
            mv = r.get("float_mcap")
            P(f"    {r['code']:<7s}{str(r['name']):<9s}{int(r['board_count']):>3d}{t:>7s}"
              f"{int(r['broken_board_count']):>4d}{r['turnover_rate']:>7.1f}{(mv/1e8 if not pd.isna(mv) else 0):>12.1f}"
              f"  {cap_flag(mv)} {board_flag(r['board_count'])}")
    P("")
    P("Next: take the final shortlist above -> python tool/cn_stock.py <code> --history to pull history -> validate entries -> fill c/watchlistd.md")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="A-share short-term candidate scanner (anti-block, slow scan)")
    ap.add_argument("--date", default=None, help="trading day YYYYMMDD; defaults to today / most recent trading day")
    ap.add_argument("--sleep", type=float, default=5.0, help="seconds to sleep after each network call (default 5; larger = safer)")
    ap.add_argument("--min-zt", type=int, default=3, help="minimum limit-up count for a sector to count as hot (default 3)")
    ap.add_argument("--top", type=int, default=8, help="show top N hot sectors in the breakdown (default 8)")
    ap.add_argument("--leaders", type=int, default=4, help="show top N leaders per sector in the breakdown (default 4)")
    ap.add_argument("--final", type=int, default=6, help="size of the final shortlist (default 6)")
    ap.add_argument("--no-cache", action="store_true", help="force fresh network fetch, ignore cache")
    ap.add_argument("--out", default="candidates", help="basename of the saved candidate list (share_data/<out>_<date>.txt); pass empty string to disable")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress stdout report (file save still happens)")
    args = ap.parse_args()

    date, df = resolve_trading_date(args.date, args.outdir, args.sleep, not args.no_cache)
    if df is None:
        print("\nERROR: limit-up pool fetch was refused or errored. Stopped per anti-block rule (no consecutive retries).")
        print("       Suggestion: run later, or raise --sleep, or switch network.")
        sys.exit(1)
    if df.empty:
        print(f"\nWARNING: {date} has no limit-up data (likely a non-trading day). Use --date to specify a trading day.")
        sys.exit(1)

    df, sector_rank, hot = analyze(df, args.min_zt, args.top)
    if hot.empty:
        print(f"\nWARNING: {date} has no sector with limit-up count >= {args.min_zt} (cold sentiment).")
        print("         Per framed.md §1: in this regime you should already be light / in cash.")
        print("         Lower --min-zt to inspect the distribution if needed.")
        # still print the sector distribution for reference
        print(sector_rank.head(args.top).to_string(index=False))
        sys.exit(0)

    leaders_per_sector = []
    for sector in hot["industry"]:
        d = pick_leaders(df[df["industry"] == sector]).head(args.leaders)
        leaders_per_sector.append((sector, d))

    final_picks = build_final_shortlist(hot, df, args.final)
    report = render(date, df, sector_rank, hot, leaders_per_sector, final_picks)
    if not args.quiet:
        print(report)

    if args.out:
        path = os.path.join(args.outdir, f"{args.out}_{date}.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(report + "\n")
        if not args.quiet:
            print(f"\nOK: candidate list saved to {path}")


if __name__ == "__main__":
    main()
