#!/usr/bin/env python3
"""
strategy_backtest.py — replay the framed.md short-term system on history and book P&L.

Agreed spec (2026-06, "Jan-2025 first" run):
  1. Trade only on GREEN gate days (framed.md §1A, via gate_backtest.replay — no network).
  2. Candidate pool for a GREEN day T = that day's SEALED limit-ups, reconstructed from OHLC.
     - Eligible = 首板 only (board_count == 1), cap-OK (float_mcap 30e8–500e8), non-ST.
     - Blanket-skip every 连板 (board_count >= 2): per J, those gap-seal and can't be bought.
  3. Pick 3, sector-first: rank sectors by # of sealed limit-ups that day (tiebreak max board,
     like scan_cn); take one eligible 首板 from each of the top sectors (diversified, one per
     sector), tie-broken within a sector by amount proxy (Volume*Close, desc). Stop at 3.
  4. Entry: next trading day (T+1) at OPEN, ¥25,000 each (100-share lots). If T+1 gap-seals at
     the open (open already at limit) -> unfillable -> skip that name.
  5. Exit: framed.md §4, mechanical —
       - TP1: intraday high >= entry*1.08 -> sell half at +8% (limit fill).
       - TP2: intraday high >= entry*1.15 -> sell rest  at +15% (limit fill).
       - Price stop: close <= entry*(1 - stop) where stop = max(5%, min(10%, 1.0× 10d-ATR%)).
         This is the Phase B calibrated ATR stop, unified across all board types.
       - Trailing: removed (2026-06-13: MA5 trail cuts winners short; net P&L +¥12K without it).
       - Time stop: on the 5th holding day, if close gain < +2% -> exit next open (Phase C recalibrated, was day-3).
       - Max hold: 10 trading days -> exit next open.
     Holding-day count is 1-based with the entry day = day 1.
  6. Output: per-trade log + Jan-2025 totals.

KNOWN LIMITATIONS (offline v1 — flagged, not hidden):
  - OHLC is qfq (前复权), not raw. For January the distortion is negligible (CN ex-div season is
    May–Aug). Sealed-limit detection uses close==high AND ratio>=limit-0.5% which is qfq-robust.
    A full-2025 run should re-pull raw for the dividend season.
  - Cap is "now" (2026) from stock_meta, not Jan-2025-accurate -> edge-of-band names may misclassify.
  - Amount tie-break uses Volume*Close (OHLC has no 成交额 column).
  - Sector heat counts only meta-covered limit-ups (~84% of codes); long-tail big boards undercounted.
  - Leader-quality fields (首封时间/炸板/封板资金) are intraday-only and NOT reconstructable, so the
    pick is a proxy of scan_cn, not scan_cn itself.
  - Sell signals that need intraday/volume context (distribution bar, broken-board re-seal fail) are
    NOT modeled; only the mechanical §4 triggers above are.

Usage:
    python strategy_backtest.py                       # Jan 2025 (default)
    python strategy_backtest.py --start 2025-01-01 --end 2025-01-31
    python strategy_backtest.py --list                # also print the daily gate verdicts used
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd

from gate_backtest import load_series, replay
from index import DEFAULT_OUTDIR, classify_index

HERE = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(HERE, "stock_history_ak")   # 948 per-stock OHLCV files (the hot_a_stocks universe)
META_CSV = os.path.join(HERE, "share_data", "stock_meta.csv")

# ---- tunables (framed.md §4/§5) ------------------------------------------
CAP_LOW, CAP_HIGH = 30e8, 500e8     # §2 friendly float-cap band (CNY) — 2026-06-13: lowered floor from 50e8
TARGET_CNY = 25_000                 # per-position notional
LOT = 100                           # A-share board lot
TP1, TP1_FRAC = 0.08, 0.5           # +8% -> sell half
TP2 = 0.15                          # +15% -> sell rest
MFE_DATA = []                       # per-trade MFE for Phase F analysis
ATR_MULT  = 1.0                     # §4A calibrated: 1.0× ATR (Phase B, all boards unified)
ATR_FLOOR = 0.05                    # §4A: 5% floor — tighter than 1×ATR on calm days
ATR_CAP   = 0.10                    # §4A: 10% cap — wider than 1×ATR on volatile days
TRAIL_ARM = 999.0                  # MA5 trail disabled (2026-06-13: removed — cuts winners short, costs ¥12K)
TRAIL_TP2 = 0.0                    # after tp1, trail remaining from peak by this % (0=off, e.g. 0.03 = -3%)
TIME_STOP_DAY, TIME_STOP_GAIN = 5, 0.02  # §4B calibrated: day-5 exit if gain < +2% (Phase C recalibrated 2026-06-13)
MAX_HOLD = 10
SEAL_TOL = 0.005                    # qfq rounding tolerance on the limit ratio
N_PICKS = 3
ACCOUNT = 75_000                    # total capital for this book (3 slots x ¥25k)
MAX_CONCURRENT = 3                  # max simultaneous positions (Phase D recalibrated 2026-06-13: 3 optimal)
SECTOR_RS_MIN = 0.0                 # only fish in sectors with 20d-mom RS percentile >= this (0 = off)
# round-trip cost: buy 0.025% comm; sell 0.025% comm + 0.05% stamp + 0.001% transfer
BUY_FEE, SELL_FEE = 0.00025, 0.00025 + 0.0005 + 0.00001


def board_limit(code):
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.startswith(("8", "4", "92")):       # BSE
        return 0.30
    return 0.10                                  # main board


# ------------------------------------------------------------------
# Load + reconstruct sealed limit-ups and consecutive-board counts
# ------------------------------------------------------------------
def load_history():
    # Stream the per-stock files in stock_history_ak/ and concat in memory —
    # no giant combined CSV on disk (the hot_a_stocks universe = these 948 files).
    files = sorted(f for f in os.listdir(HIST_DIR) if f.endswith(".csv"))
    if not files:
        sys.exit(f"no per-stock CSVs in {HIST_DIR} (run cn_stock.py --history / downa.py first)")
    parts = []
    for fn in files:
        try:
            parts.append(pd.read_csv(os.path.join(HIST_DIR, fn), dtype={"symbol": str}))
        except Exception:
            continue
    df = pd.concat(parts, ignore_index=True)
    df["symbol"] = df["symbol"].str.zfill(6)
    df["Date"] = df["Date"].astype(str)
    df = df.sort_values(["symbol", "Date"]).reset_index(drop=True)
    return df


def load_meta():
    m = pd.read_csv(META_CSV, dtype={"code": str})
    m["code"] = m["code"].str.zfill(6)
    m["float_mcap_now"] = pd.to_numeric(m["float_mcap_now"], errors="coerce")
    m = m.set_index("code")
    return m


def consecutive(seals):
    out, c = [], 0
    for v in seals:
        c = c + 1 if v else 0
        out.append(c)
    return out


def annotate(df):
    """Add prev_close, ratio, atr_pct, sealed, board_count per symbol."""
    frames = []
    for code, g in df.groupby("symbol", sort=False):
        g = g.copy()
        lim = board_limit(code)
        prevc = g["Close"].shift(1)
        ratio = g["Close"] / prevc - 1.0
        sealed = ((g["High"] - g["Close"]).abs() < 1e-6) & (ratio >= lim - SEAL_TOL) & (ratio <= lim + 0.03)
        g["prev_close"] = prevc
        g["ratio"] = ratio
        g["sealed"] = sealed.fillna(False)
        g["board_count"] = consecutive(g["sealed"].tolist())
        g["ma5"] = g["Close"].rolling(5).mean()
        g["limit"] = lim
        # 10-day ATR as % of close (Phase B calibrated: 1.0× multiplier, 5% floor, 10% cap)
        tr = pd.concat([g["High"] - g["Low"],
                        (g["High"] - prevc).abs(),
                        (g["Low"]  - prevc).abs()], axis=1).max(axis=1)
        g["atr_pct"] = tr.rolling(10).mean() / g["Close"]
        frames.append(g)
    return pd.concat(frames).reset_index(drop=True)


# ------------------------------------------------------------------
# Sector relative-strength: per (Date, industry) the 20d-momentum RS percentile within the day.
# Built from the whole universe (free, no external data). Used as a FILTER, not a ranker.
# ------------------------------------------------------------------
def build_sector_pct(hist, meta):
    h = hist[["Date", "symbol", "Close"]].copy()
    h["industry"] = h["symbol"].map(meta["industry"])
    h = h[h["industry"].notna()].sort_values(["symbol", "Date"])
    h["smom20"] = h["Close"] / h.groupby("symbol")["Close"].shift(20) - 1.0
    sec = h.dropna(subset=["smom20"]).groupby(["Date", "industry"])["smom20"].mean().reset_index()
    sec["pct"] = sec.groupby("Date")["smom20"].rank(pct=True)
    return {(r.Date, r.industry): r.pct for r in sec.itertuples(index=False)}


# ------------------------------------------------------------------
# Candidate selection for one GREEN day
# ------------------------------------------------------------------
def pick_candidates(day_rows, meta, sec_pct=None, no_sector_diversify=False):
    """day_rows: all rows on day T. Return up to N_PICKS eligible 首板 cap-OK names,
    sector-first + amount tie-break. If sec_pct given and SECTOR_RS_MIN>0, only keep
    candidates whose sector RS percentile that day >= SECTOR_RS_MIN (hot-sector filter)."""
    s = day_rows[day_rows["sealed"]].copy()
    if s.empty:
        return [], pd.DataFrame()
    s["industry"] = s["symbol"].map(meta["industry"])
    s["name"] = s["symbol"].map(meta["name"])
    s["cap"] = s["symbol"].map(meta["float_mcap_now"])
    s = s[s["industry"].notna()]                                   # meta-covered only
    s = s[~s["name"].astype(str).str.contains("ST|退", na=False)]   # exclude ST/退
    if s.empty:
        return [], s

    # sector heat over ALL sealed limit-ups (any board), like scan_cn
    heat = s.groupby("industry").agg(zt=("symbol", "count"),
                                     maxbd=("board_count", "max")).reset_index()
    heat = heat.sort_values(["zt", "maxbd"], ascending=False).reset_index(drop=True)

    s["amount"] = s["Volume"] * s["Close"]
    elig = s[(s["board_count"] == 1) & (s["cap"].between(CAP_LOW, CAP_HIGH))].copy()

    # hot-sector FILTER (the one validated signal): drop candidates in cold sectors
    if sec_pct is not None and SECTOR_RS_MIN > 0 and not elig.empty:
        T = day_rows["Date"].iloc[0]
        elig = elig[elig["industry"].map(lambda ind: sec_pct.get((T, ind), 0.0) >= SECTOR_RS_MIN)]

    picks = []
    if no_sector_diversify:
        # top N by amount across all sectors, no sector constraint
        top = elig.sort_values("amount", ascending=False)
        picks = [top.iloc[i] for i in range(min(N_PICKS, len(top)))]
    else:
        for sector in heat["industry"].tolist():
            cand = elig[elig["industry"] == sector].sort_values("amount", ascending=False)
            if not cand.empty:
                picks.append(cand.iloc[0])
            if len(picks) >= N_PICKS:
                break
    return picks, s


# ------------------------------------------------------------------
# Exit engine (framed.md §4) for one position
# ------------------------------------------------------------------
def simulate(series, entry_pos, entry_price, shares):
    """series: symbol's annotated rows (list of dicts), entry_pos: index of entry day.
    Returns list of fills [(shares, price, reason)] and the last exit date."""
    fills = []
    remaining = shares
    tp1_done = False
    armed = False
    pending = None          # (reason) scheduled to sell at next open
    last_date = series[entry_pos]["Date"]
    mfe_pct = 0.0           # max favorable excursion (max H / entry - 1)
    peak_since_tp1 = 0.0    # high-water mark after tp1, for trailing stop
    n = 0
    pos = entry_pos
    while pos < len(series) and remaining > 0:
        row = series[pos]
        last_date = row["Date"]
        n += 1
        O, H, C = row["Open"], row["High"], row["Close"]

        # track MFE
        day_high_pct = H / entry_price - 1.0
        if day_high_pct > mfe_pct:
            mfe_pct = day_high_pct

        # 0. execute a previously-scheduled next-open exit
        if pending is not None:
            fills.append((remaining, O, pending))
            remaining = 0
            break

        # 1. intraday take-profit (limit fills)
        tp1_label = f"tp1+{int(TP1*100)}%"
        tp2_label = f"tp2+{int(TP2*100)}%"
        if not tp1_done and H >= entry_price * (1 + TP1):
            half = (shares // (2 * LOT)) * LOT
            if half > 0:
                fills.append((half, entry_price * (1 + TP1), tp1_label))
                remaining -= half
            tp1_done = True
            peak_since_tp1 = H   # start tracking peak for trailing tp2

        # track peak after tp1 for trailing stop
        if tp1_done and TRAIL_TP2 > 0 and H > peak_since_tp1:
            peak_since_tp1 = H
        if remaining > 0 and H >= entry_price * (1 + TP2):
            fills.append((remaining, entry_price * (1 + TP2), tp2_label))
            remaining = 0
            break

        # 2. close-based signals -> schedule next-open exit
        gain_c = C / entry_price - 1.0
        if gain_c > TRAIL_ARM:
            armed = True
        # dynamic ATR-based stop (Phase B calibrated): 1.0× ATR, floored at 5%, capped at 10%
        atr = row.get("atr_pct", None)
        if atr is None or pd.isna(atr):
            atr = 0.07  # fallback: default ~7% for first 10 days of data
        stop = max(ATR_FLOOR, min(ATR_CAP, ATR_MULT * atr))
        reason = None
        if C <= entry_price * (1 - stop):
            reason = "stop-atr"
        elif tp1_done and TRAIL_TP2 > 0 and peak_since_tp1 > 0 and C <= peak_since_tp1 * (1 - TRAIL_TP2):
            reason = f"trailtp2-{int(TRAIL_TP2*100)}%"
        elif armed and not pd.isna(row["ma5"]) and C < row["ma5"]:
            reason = "trail5ma"
        elif n == TIME_STOP_DAY and gain_c < TIME_STOP_GAIN:
            reason = f"timed{TIME_STOP_DAY}d"
        elif n >= MAX_HOLD:
            reason = "maxhold10"
        if reason:
            pending = reason
        pos += 1

    # ran out of data while holding -> mark to close at last close
    if remaining > 0:
        fills.append((remaining, series[len(series) - 1]["Close"], "dataend"))
        last_date = series[len(series) - 1]["Date"]
    return fills, last_date, mfe_pct


# ------------------------------------------------------------------
def main():
    global ACCOUNT, MAX_CONCURRENT, SECTOR_RS_MIN, TIME_STOP_DAY, TIME_STOP_GAIN, TP1, TP2, TRAIL_ARM, MAX_HOLD, CAP_LOW, CAP_HIGH, TRAIL_TP2, TARGET_CNY
    ap = argparse.ArgumentParser(description="Replay framed.md system, book P&L (offline)")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-01-31")
    ap.add_argument("--list", action="store_true", help="print the daily gate verdicts used")
    ap.add_argument("--account", type=int, default=ACCOUNT, help="total capital")
    ap.add_argument("--slots", type=int, default=MAX_CONCURRENT, help="max concurrent positions")
    ap.add_argument("--rs", type=float, default=SECTOR_RS_MIN,
                    help="sector RS percentile floor (0 = filter off, e.g. 0.7 = top-30%% sectors)")
    ap.add_argument("--time-stop-day", type=int, default=TIME_STOP_DAY,
                    help=f"day to trigger time stop (default {TIME_STOP_DAY})")
    ap.add_argument("--time-stop-gain", type=float, default=TIME_STOP_GAIN,
                    help=f"gain threshold for time stop (default {TIME_STOP_GAIN})")
    ap.add_argument("--min-green-streak", type=int, default=1,
                    help="require N consecutive GREEN days to trade (1=off, 2+=filter)")
    ap.add_argument("--require-csi300", action="store_true",
                    help="require CSI300 index also GREEN (not just overall light)")
    ap.add_argument("--tp1", type=float, default=TP1,
                    help=f"take-profit 1 threshold (default {TP1})")
    ap.add_argument("--tp2", type=float, default=TP2,
                    help=f"take-profit 2 threshold (default {TP2})")
    ap.add_argument("--no-trail", action="store_true",
                    help="disable 5MA trailing stop (arm at +99900%%)")
    ap.add_argument("--max-hold", type=int, default=MAX_HOLD,
                    help=f"max holding days (default {MAX_HOLD})")
    ap.add_argument("--cap-low", type=float, default=CAP_LOW,
                    help=f"float cap lower bound in CNY (default {CAP_LOW})")
    ap.add_argument("--cap-high", type=float, default=CAP_HIGH,
                    help=f"float cap upper bound in CNY (default {CAP_HIGH})")
    ap.add_argument("--include-amber", action="store_true",
                    help="also trade on AMBER gate days (not just GREEN)")
    ap.add_argument("--no-sector-diversify", action="store_true",
                    help="pick top N by amount across all sectors (ignore sector diversity)")
    ap.add_argument("--trail-tp2", type=float, default=TRAIL_TP2,
                    help=f"after tp1, trail remaining from peak by this %% (0=off, default {TRAIL_TP2})")
    ap.add_argument("--per-slot", type=int, default=TARGET_CNY,
                    help=f"per-position notional (default {TARGET_CNY})")
    args = ap.parse_args()
    ACCOUNT, MAX_CONCURRENT, SECTOR_RS_MIN = args.account, args.slots, args.rs
    TIME_STOP_DAY, TIME_STOP_GAIN = args.time_stop_day, args.time_stop_gain
    TP1, TP2 = args.tp1, args.tp2
    MAX_HOLD = args.max_hold
    TRAIL_TP2 = args.trail_tp2
    TARGET_CNY = args.per_slot
    CAP_LOW, CAP_HIGH = args.cap_low, args.cap_high
    if args.no_trail:
        TRAIL_ARM = 999.0

    # 1. gate
    gseries = load_series(DEFAULT_OUTDIR)
    verdicts = replay(gseries, args.start, args.end)

    # per-index lights for CSI300 filter
    per_index = {}
    if args.require_csi300:
        base = gseries["000001"]
        all_idx_dates = sorted(base["Date"].unique())
        for d in [d for d in all_idx_dates if args.start <= d <= args.end]:
            per = {}
            ok = True
            for code, df in gseries.items():
                sub = df[df["Date"] <= d]
                if len(sub) < 12 or sub["Date"].iloc[-1] != d:
                    ok = False
                    break
                per[code] = classify_index(sub)
            if ok and all(per.values()):
                per_index[d] = per

    # CSI300 filter
    if args.require_csi300:
        before = len(verdicts)
        verdicts = [(d, v) for d, v in verdicts
                    if per_index.get(d, {}).get("000300") == "GREEN"]
        after = len(verdicts)
        print(f"CSI300 filter: {before} → {after} trading days ({before - after} removed)")

    green = [d for d, v in verdicts if v == "GREEN" or (args.include_amber and v == "AMBER")]

    # min-green-streak filter
    if args.min_green_streak > 1:
        # compute streaks
        streak_map = {}
        cur = 0
        for d, v in verdicts:
            cur = cur + 1 if v == "GREEN" else 0
            streak_map[d] = cur
        before = len(green)
        green = [d for d in green if streak_map[d] >= args.min_green_streak]
        print(f"Min streak {args.min_green_streak}: {before} → {len(green)} GREEN days ({before - len(green)} removed)")
    if args.list:
        for d, v in verdicts:
            print(f"  {d}  {v}")
        print("")
    print(f"Gate: {len(verdicts)} trading days, GREEN = {green}")

    # 2. history + reconstruction
    print("Loading history + reconstructing sealed limit-ups ...")
    hist = annotate(load_history())
    meta = load_meta()
    sec_pct = build_sector_pct(hist, meta) if SECTOR_RS_MIN > 0 else None
    by_code = {c: g.reset_index(drop=True) for c, g in hist.groupby("symbol", sort=False)}
    all_dates = sorted(hist["Date"].unique())

    def next_trading_date(d):
        i = all_dates.index(d) if d in all_dates else None
        return all_dates[i + 1] if (i is not None and i + 1 < len(all_dates)) else None

    # 3. per GREEN day: pick -> enter T+1 -> simulate each candidate (exits = framed.md §4)
    cands = []
    for T in green:
        day_rows = hist[hist["Date"] == T]
        picks, _ = pick_candidates(day_rows, meta, sec_pct, args.no_sector_diversify)
        T1 = next_trading_date(T)
        for rank, r in enumerate(picks):
            code = r["symbol"]
            g = by_code[code]
            entry_rows = g.index[g["Date"] == T1].tolist()
            if not T1 or not entry_rows:
                cands.append(dict(T=T, code=code, name=r["name"], sector=r["industry"],
                                  skip="no T+1 row"))
                continue
            epos = entry_rows[0]
            erow = g.loc[epos]
            lim = board_limit(code)
            # unfillable: T+1 opens at/above the limit (gap-seal)
            if erow["Open"] / r["Close"] - 1.0 >= lim - SEAL_TOL:
                cands.append(dict(T=T, code=code, name=r["name"], sector=r["industry"],
                                  skip="T+1 gap-seal (unfillable)"))
                continue
            entry = erow["Open"]
            shares = int((TARGET_CNY / entry) // LOT * LOT)
            if shares < LOT:
                cands.append(dict(T=T, code=code, name=r["name"], sector=r["industry"],
                                  skip="too pricey for 1 lot"))
                continue
            series = g.to_dict("records")
            fills, last_date, mfe_pct = simulate(series, epos, entry, shares)

            cost = entry * shares
            buy_fee = cost * BUY_FEE
            proceeds = sum(sh * px for sh, px, _ in fills)
            sell_fee = proceeds * SELL_FEE
            net = proceeds - cost - buy_fee - sell_fee
            wexit = proceeds / shares                       # weighted avg exit
            cands.append(dict(T=T, code=code, name=r["name"], sector=r["industry"], rank=rank,
                              entry_date=erow["Date"], entry=entry, shares=shares,
                              exit_date=last_date, wexit=wexit,
                              legs="+".join(f"{sh}@{px:.2f}{rs}" for sh, px, rs in fills),
                              cost=cost, buy_fee=buy_fee, net=net, pct=net / cost * 100,
                              mfe=mfe_pct))

    # 3b. PORTFOLIO pass — the real constraint: max MAX_CONCURRENT slots, ¥ACCOUNT cash.
    #     Skip any signal when both slots are busy / cash is short; an exit frees slot + cash.
    #     (Per-trade exits are unchanged framed.md §4 — this only gates ENTRY.)
    sims = sorted((c for c in cands if "skip" not in c),
                  key=lambda c: (c["entry_date"], c.get("rank", 0)))
    cash = ACCOUNT
    cash_lo = ACCOUNT
    open_pos = []          # each: dict(exit_date, ret_cash)
    trades = []            # taken
    cap_skips = []
    for c in sims:
        # release positions that have exited on/before this entry date
        keep = []
        for p in open_pos:
            if p["exit_date"] <= c["entry_date"]:
                cash += p["ret_cash"]
            else:
                keep.append(p)
        open_pos = keep
        if len(open_pos) >= MAX_CONCURRENT:
            c["skip"] = "slots full (2 concurrent)"
            cap_skips.append(c)
            continue
        if cash < c["cost"] + c["buy_fee"]:
            c["skip"] = "cash short (slot unfunded)"
            cap_skips.append(c)
            continue
        cash -= (c["cost"] + c["buy_fee"])
        cash_lo = min(cash_lo, cash)
        open_pos.append(dict(exit_date=c["exit_date"], ret_cash=c["cost"] + c["net"] + c["buy_fee"]))
        trades.append(c)
    for p in open_pos:                                       # close out any still open at range end
        cash += p["ret_cash"]

    other_skips = [c for c in cands if c.get("skip") and c not in cap_skips]

    # 4. report
    print("\n" + "=" * 92)
    print(f" framed.md system backtest   {args.start} .. {args.end}   "
          f"(¥{ACCOUNT:,} acct, max {MAX_CONCURRENT} x ¥{TARGET_CNY:,}, sectorRS>={SECTOR_RS_MIN})")
    print("=" * 92)
    done = trades
    skipped = other_skips
    hdr = f"{'GREEN':<11}{'entry':<11}{'code':<7}{'name':<9}{'sector':<12}{'entry':>7}{'wexit':>7}{'sh':>6}{'net¥':>9}{'pct':>7}  {'mfe':>7}  exit"
    print(hdr)
    print("-" * 92)
    for t in done:
        print(f"{t['T']:<11}{t['entry_date']:<11}{t['code']:<7}{str(t['name']):<9}{str(t['sector'])[:11]:<12}"
              f"{t['entry']:>7.2f}{t['wexit']:>7.2f}{t['shares']:>6}{t['net']:>9.0f}{t['pct']:>6.1f}%"
              f"  {t.get('mfe',0)*100:>+6.1f}%  {t['legs']}")
    if skipped:
        print(f"\nSkipped (data/fill reasons): {len(skipped)}")
        for t in skipped:
            print(f"  {t['T']}  {t['code']} {t['name']}  ({t['sector']})  -> {t['skip']}")
    if cap_skips:
        print(f"\nSkipped (no free slot / cash — the ¥{ACCOUNT:,} cap doing its job): {len(cap_skips)}")

    if done:
        tot_net = sum(t["net"] for t in done)
        tot_cost = sum(t["entry"] * t["shares"] for t in done)
        wins = [t for t in done if t["net"] > 0]
        ret_acct = tot_net / ACCOUNT * 100
        print("-" * 92)
        print(f" trades {len(done)}   wins {len(wins)} ({len(wins)/len(done)*100:.0f}%)   "
              f"turnover ¥{tot_cost:,.0f}   lowest cash ¥{cash_lo:,.0f}")
        print(f" net P&L ¥{tot_net:,.0f}   on ¥{ACCOUNT:,} account = {ret_acct:+.1f}%   "
              f"(final cash ¥{cash:,.0f})")

        # Phase F: take-profit band sweep using per-trade MFE data
        print("\n" + "=" * 92)
        print(" Phase F — Take-profit band sweep (simulated from trade MFE)")
        print("=" * 92)
        print(f" {'tp1':>6s}  {'tp2':>6s}  {'net_pnl':>10s}  {'win%':>6s}  {'avg_win':>9s}  {'avg_loss':>9s}  {'expectancy':>10s}")
        print(f" {'-'*6}  {'-'*6}  {'-'*10}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*10}")

        tp_sweeps = [
            (0.06, 0.12), (0.06, 0.15), (0.08, 0.12), (0.08, 0.15), (0.08, 0.18),
            (0.10, 0.15), (0.10, 0.18), (0.10, 0.20), (0.12, 0.18), (0.12, 0.20),
            (0.15, 0.20), (0.15, 0.25),
        ]
        # baseline: current (0.08, 0.15)
        tp_sweeps.insert(0, (TP1, TP2))

        TARGET = TARGET_CNY  # slot size

        for tp1, tp2 in tp_sweeps:
            simulated_nets = []
            for t in done:
                entry = t["entry"]
                cost = entry * t["shares"]
                mfe = t.get("mfe", 0)
                actual_net = t["net"]
                actual_pct = t["pct"]

                # Simulate: if MFE >= tp1, capture tp1 on half; else actual
                #            if MFE >= tp2, capture tp2 on rest; else actual
                half_shares = t["shares"] // 2
                rest_shares = t["shares"] - half_shares

                # TP1 on first half
                if mfe >= tp1:
                    tp1_proceeds = half_shares * entry * (1 + tp1)
                else:
                    tp1_proceeds = half_shares * entry * (1 + actual_pct / 100)
                # TP2 on rest
                if mfe >= tp2:
                    tp2_proceeds = rest_shares * entry * (1 + tp2)
                else:
                    tp2_proceeds = rest_shares * entry * (1 + actual_pct / 100)

                sim_proceeds = tp1_proceeds + tp2_proceeds
                buy_fee = cost * BUY_FEE
                sell_fee = sim_proceeds * SELL_FEE
                sim_net = sim_proceeds - cost - buy_fee - sell_fee
                simulated_nets.append(sim_net)

            total_sim = sum(simulated_nets)
            wins_sim = [n for n in simulated_nets if n > 0]
            losses_sim = [n for n in simulated_nets if n <= 0]
            win_pct = len(wins_sim) / len(simulated_nets) * 100 if simulated_nets else 0
            avg_win = sum(wins_sim) / len(wins_sim) if wins_sim else 0
            avg_loss = sum(losses_sim) / len(losses_sim) if losses_sim else 0
            exp_val = total_sim / len(simulated_nets) if simulated_nets else 0

            marker = " ← current" if (tp1 == TP1 and tp2 == TP2) else ""
            print(f" {tp1*100:>+5.0f}%  {tp2*100:>+5.0f}%  ¥{total_sim:>9,.0f}  {win_pct:>5.1f}%  ¥{avg_win:>8,.0f}  ¥{avg_loss:>8,.0f}  ¥{exp_val:>9,.0f}{marker}")
    else:
        print("\nNo trades executed.")


if __name__ == "__main__":
    main()
