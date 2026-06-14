#!/usr/bin/env python3
"""
winner_study.py — reverse-engineer what precedes big A-share moves ("hunting, not filtering").

Instead of guessing the pre_break gates and tuning them, this does the opposite: it finds
every stock-day that went on to a BIG forward move, then asks what those days looked like
BEFORE the move — and, crucially, compares that to the whole population so we don't fall for
survivorship bias.

Definitions (buyable, matches grade_6test):
    entry = next trading day's OPEN, exit = CLOSE of the 10th trading day
    forward return = (close[D+10] - open[D+1]) / open[D+1] * 100
    WINNER = forward return >= WIN_PCT (default +30%)

For every gradeable stock-day it snapshots PRECURSOR features computed with data up to and
including day D (no look-ahead): distance from 20/60/250-day highs, volume expansion, MA
structure, momentum, base tightness, market cap. Then for each feature it bins the WHOLE
population and reports the winner-rate per bin vs the base rate — so a feature only looks
good if winners cluster there MORE than the crowd does (that ratio is the "lift").

Output: one file -> share_data/winner_study.txt   (no other files). All data is local.

Usage:
    python winner_study.py                 # +30% bar, full local history
    python winner_study.py --win 20        # different winner bar
    python winner_study.py --from 2024-01-01 --to 2025-12-31   # restrict window
"""

import os
import sys
import argparse
import csv

import numpy as np
import pandas as pd

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
META_PATH = os.path.join(TOOL_DIR, "share_data", "stock_meta.csv")
SHARE_DIR = os.path.join(TOOL_DIR, "share_data")
OUT_PATH = os.path.join(SHARE_DIR, "winner_study.txt")

HOLD = 10  # trading days

# precursor features: (column, label, kind)  kind in {"cont","bool"}
FEATURES = [
    ("pct_from_high20",  "dist from 20d high (%)",    "cont"),
    ("pct_from_high60",  "dist from 60d high (%)",    "cont"),
    ("pct_from_high250", "dist from 250d high (%)",   "cont"),
    ("vol_ratio5",       "volume / prior 5d avg",     "cont"),
    ("vol_ratio20",      "volume / prior 20d avg",    "cont"),
    ("mom5",             "5-day momentum (%)",        "cont"),
    ("mom20",            "20-day momentum (%)",       "cont"),
    ("range_contract",   "5d range / 20d range",      "cont"),
    ("turnover_yi",      "daily turnover (100M yuan)", "cont"),
    ("mcap_b",           "float mcap (B yuan)",       "cont"),
    ("ma_aligned",       "MA 5>10>20 aligned",        "bool"),
    ("above_ma20",       "close above 20d MA",        "bool"),
]


def load_mcap() -> dict:
    """code -> float market cap in yuan (current snapshot only; historical not available)."""
    out = {}
    if not os.path.exists(META_PATH):
        return out
    with open(META_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row.get("code", "").strip()] = float(row.get("float_mcap_now", "0") or "0")
            except ValueError:
                pass
    return out


def features_for_stock(df: pd.DataFrame, mcap: float) -> pd.DataFrame:
    """Vectorized precursor features + forward return for one stock. No look-ahead."""
    df = df.sort_values("Date").reset_index(drop=True)
    c, h, l, v, o = df["Close"], df["High"], df["Low"], df["Volume"], df["Open"]

    high20, high60, high250 = h.rolling(20).max(), h.rolling(60).max(), h.rolling(250).max()
    ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
    vol5_prior = v.shift(1).rolling(5).mean()
    vol20_prior = v.shift(1).rolling(20).mean()
    rng = h - l
    rng5, rng20 = rng.rolling(5).mean(), rng.rolling(20).mean()

    entry = o.shift(-1)                 # D+1 open
    exit_ = c.shift(-HOLD)              # D+HOLD close
    fwd = (exit_ - entry) / entry * 100

    out = pd.DataFrame({
        "fwd": fwd,
        "pct_from_high20":  (c - high20) / high20 * 100,
        "pct_from_high60":  (c - high60) / high60 * 100,
        "pct_from_high250": (c - high250) / high250 * 100,
        "vol_ratio5":  v / vol5_prior,
        "vol_ratio20": v / vol20_prior,
        "mom5":  (c / c.shift(5) - 1) * 100,
        "mom20": (c / c.shift(20) - 1) * 100,
        "range_contract": rng5 / rng20,
        "turnover_yi": v * 100 * c / 1e8,   # vol(lots)*100 shares * price, in 100M yuan
        "mcap_b": mcap / 1e9 if mcap else np.nan,
        "ma_aligned":  (ma5 > ma10) & (ma10 > ma20),
        "above_ma20":  c > ma20,
        "close": c,
        "entry_open": entry,
        "Date": df["Date"],
    })
    # require enough lookback (60d high present) and a real forward return
    out = out[high60.notna().values & out["fwd"].notna()]
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


def build_dataset(date_from, date_to) -> pd.DataFrame:
    mcap = load_mcap()
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))
    frames = []
    for i, fn in enumerate(files, 1):
        if i % 200 == 0:
            print(f"  ... {i}/{len(files)} stocks", file=sys.stderr)
        code = fn[:-4]
        try:
            df = pd.read_csv(os.path.join(HISTORY_DIR, fn), parse_dates=["Date"])
        except Exception:
            continue
        if df.empty or "Close" not in df.columns:
            continue
        feat = features_for_stock(df, mcap.get(code, np.nan))
        if date_from is not None:
            feat = feat[feat["Date"] >= date_from]
        if date_to is not None:
            feat = feat[feat["Date"] <= date_to]
        if not feat.empty:
            feat["code"] = code
            frames.append(feat)
    if not frames:
        sys.exit("no data collected")
    return pd.concat(frames, ignore_index=True)


def analyze(data: pd.DataFrame, win_pct: float) -> str:
    data = data.copy()
    data["winner"] = data["fwd"] >= win_pct
    base = data["winner"].mean() * 100
    n = len(data)

    lines = []
    lines.append(f"winner_study — what precedes a +{win_pct:.0f}% move in {HOLD} market days")
    lines.append(f"(entry next-day open, exit day-{HOLD} close)")
    lines.append("=" * 64)
    lines.append(f"stock-days analyzed .. {n:,}")
    lines.append(f"winners (>= +{win_pct:.0f}%) .. {int(data['winner'].sum()):,}")
    lines.append(f"BASE RATE ............ {base:.2f}%   <- a feature is only useful if it BEATS this")
    lines.append("")

    # score each feature by its best lift, so we can rank, then print detail in rank order
    blocks = []  # (max_lift, text)
    for col, label, kind in FEATURES:
        sub = data[[col, "winner"]].dropna()
        if sub.empty:
            continue
        buf = [f"## {label}   ({col})"]
        if kind == "bool":
            g = sub.groupby(col)["winner"]
            rate, cnt = g.mean() * 100, g.size()
            max_lift = 0.0
            buf.append(f"  {'value':<8}{'n':>9}{'win%':>9}{'lift':>8}")
            for val in [True, False]:
                if val in rate.index:
                    lift = rate[val] / base if base > 0 else 0
                    max_lift = max(max_lift, lift)
                    buf.append(f"  {str(val):<8}{cnt[val]:>9,}{rate[val]:>8.2f}%{lift:>7.1f}x")
        else:
            try:
                sub = sub.copy()
                sub["bin"] = pd.qcut(sub[col], 6, duplicates="drop")
            except Exception:
                continue
            g = sub.groupby("bin", observed=True)["winner"]
            rate, cnt = g.mean() * 100, g.size()
            max_lift = (rate.max() / base) if base > 0 else 0
            buf.append(f"  {'range':<24}{'n':>9}{'win%':>9}{'lift':>8}")
            for interval in rate.index:
                lift = rate[interval] / base if base > 0 else 0
                rng_txt = f"[{interval.left:.1f}, {interval.right:.1f}]"
                buf.append(f"  {rng_txt:<24}{cnt[interval]:>9,}{rate[interval]:>8.2f}%{lift:>7.1f}x")
        blocks.append((max_lift, "\n".join(buf)))

    blocks.sort(key=lambda b: b[0], reverse=True)
    lines.append("FEATURES RANKED BY BEST LIFT (how much a bin beats the base rate)")
    lines.append("-" * 64)
    for _, text in blocks:
        lines.append(text)
        lines.append("")

    lines.append("Read: 'lift 3.0x' = winners are 3x more concentrated in that bin than the")
    lines.append("base rate. High-lift bins are candidate GATES. Validate on a held-out period")
    lines.append("before trusting any of them.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Reverse-engineer pre-breakout features from real winners")
    ap.add_argument("--win", type=float, default=30.0, help="winner bar, %% forward return (default 30)")
    ap.add_argument("--from", dest="date_from", help="start date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="end date YYYY-MM-DD")
    args = ap.parse_args()

    date_from = pd.Timestamp(args.date_from) if args.date_from else None
    date_to = pd.Timestamp(args.date_to) if args.date_to else None

    print("Building dataset (vectorized, all local history)...", file=sys.stderr)
    data = build_dataset(date_from, date_to)
    report = analyze(data, args.win)

    print("\n" + report)
    os.makedirs(SHARE_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\nSaved -> {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
