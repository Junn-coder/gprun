#!/usr/bin/env python3
"""
build_turn_dataset.py — prepare the training dataset for the "pure-up turn" hunt.

Each example is one stock-day D = the PICK point = end of a `before`-day window
(period - 10; period default 30, so before = 20). Everything is computed with data
up to and including day D — no look-ahead.

  Features (all the picker may see at decision time):
    r0..r{B-1}   daily return % over the before-window  (r{B-1} = day D, the newest)
    vr0..vr{B-1} that day's volume / the before-window average volume (stock-agnostic)
    mcap_b       float market cap (B yuan), carried so pre_break can report cap band

  Label = "loose pure-up +10%" over the next 10 trading days:
    entry = open[D+1], exit = close[D+10]
    net   = (exit - entry) / entry * 100 >= WIN_PCT
    AND the path never closes more than DD_TOL% below entry (a real ride, not a spike)

  fwd_net is kept for inspection ("what 'up' looks like").

Output: share_data/turn_dataset.parquet (or .csv.gz fallback). All data local.

Usage:
    python build_turn_dataset.py                 # period 30 (before 20), +10%, dd 5%
    python build_turn_dataset.py --period 40     # longer before-window (30 days)
    python build_turn_dataset.py --win 10 --dd 5
"""
import os
import sys
import csv
import argparse

import numpy as np
import pandas as pd

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
META_PATH = os.path.join(TOOL_DIR, "share_data", "stock_meta.csv")
SHARE_DIR = os.path.join(TOOL_DIR, "share_data")

HOLD = 10  # the up-leg (fixed)


def load_mcap() -> dict:
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


def build_one(df: pd.DataFrame, mcap, before: int, hold: int, win_pct: float, dd_tol: float):
    df = df.sort_values("Date").reset_index(drop=True)
    o, c, v = df["Open"], df["Close"], df["Volume"]

    ret = (c / c.shift(1) - 1) * 100
    volavg = v.rolling(before).mean()

    # forward label (no look-ahead: uses only days strictly after D)
    entry = o.shift(-1)
    exit_ = c.shift(-hold)
    fut_min = pd.concat([c.shift(-i) for i in range(1, hold + 1)], axis=1).min(axis=1)
    net = (exit_ - entry) / entry * 100
    dd_ok = fut_min >= entry * (1 - dd_tol / 100.0)
    label = ((net >= win_pct) & dd_ok).astype("int8")

    data = {
        "Date": df["Date"],
        "fwd_net": net,
        "label": label,
        "mcap_b": (mcap / 1e9 if mcap else np.nan),
    }
    feat_cols = []
    for k in range(before):                 # r0 = oldest, r{before-1} = day D
        data[f"r{k}"] = ret.shift(before - 1 - k)
        data[f"vr{k}"] = v.shift(before - 1 - k) / volavg
        feat_cols += [f"r{k}", f"vr{k}"]

    out = pd.DataFrame(data).replace([np.inf, -np.inf], np.nan)
    # need full feature window + a real forward label; mcap_b may stay NaN (it's a feature)
    out = out[entry.notna().values & exit_.notna().values]
    out = out.dropna(subset=feat_cols)
    return out, feat_cols


def main():
    ap = argparse.ArgumentParser(description="Prepare the pure-up turn training dataset")
    ap.add_argument("--period", type=int, default=30, help="window length; before = period-10 (default 30)")
    ap.add_argument("--win", type=float, default=10.0, help="pure-up bar %% over 10 days (default 10)")
    ap.add_argument("--dd", type=float, default=5.0, help="max %% a close may sit below entry (default 5)")
    args = ap.parse_args()

    before = args.period - HOLD
    if before < 5:
        sys.exit(f"period {args.period} too short (before window = {before})")

    mcap = load_mcap()
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))
    print(f"Building turn dataset: period={args.period} (before={before}d), "
          f"win=+{args.win:.0f}%, dd<={args.dd:.0f}%, over {len(files)} stocks...", file=sys.stderr)

    frames, feat_cols = [], None
    for i, fn in enumerate(files, 1):
        if i % 200 == 0:
            print(f"  ... {i}/{len(files)} stocks", file=sys.stderr)
        code = fn[:-4]
        try:
            df = pd.read_csv(os.path.join(HISTORY_DIR, fn), parse_dates=["Date"])
        except Exception:
            continue
        if df.empty or "Close" not in df.columns or len(df) < before + HOLD + 2:
            continue
        out, fc = build_one(df, mcap.get(code, np.nan), before, args.win, args.dd)
        feat_cols = fc
        if not out.empty:
            out.insert(0, "code", code)
            frames.append(out)

    if not frames:
        sys.exit("no data collected")
    data = pd.concat(frames, ignore_index=True)

    pos = int(data["label"].sum())
    n = len(data)
    rate = pos / n * 100

    # save (parquet preferred, csv.gz fallback)
    os.makedirs(SHARE_DIR, exist_ok=True)
    out_path = os.path.join(SHARE_DIR, "turn_dataset.parquet")
    try:
        data.to_parquet(out_path, index=False)
    except Exception as e:
        out_path = os.path.join(SHARE_DIR, "turn_dataset.csv.gz")
        data.to_csv(out_path, index=False, compression="gzip")
        print(f"(parquet unavailable: {e}; wrote csv.gz)", file=sys.stderr)

    # report
    print("\n" + "=" * 60)
    print(f"TURN DATASET  period={args.period} (before={before}d)  label=+{args.win:.0f}%/{HOLD}d pure-up (dd<={args.dd:.0f}%)")
    print("=" * 60)
    print(f"stock-days (rows) ... {n:,}")
    print(f"positives (turners) . {pos:,}")
    print(f"negatives ........... {n - pos:,}")
    print(f"base rate ........... {rate:.2f}%")
    print(f"feature cols ........ {len(feat_cols)}  (r0..r{before-1}, vr0..vr{before-1}) + mcap_b")
    print(f"saved -> {out_path}")

    # cap-band breakdown for THIS label
    sub = data.dropna(subset=["mcap_b"]).copy()
    if not sub.empty:
        sub["band"] = pd.qcut(sub["mcap_b"], 6, duplicates="drop")
        g = sub.groupby("band", observed=True)["label"]
        brate, bn = g.mean() * 100, g.size()
        base = sub["label"].mean() * 100
        print("\nWhere are the fish? positive-rate by float-mcap band (this label):")
        print(f"  {'mcap band (B yuan)':<26}{'n':>10}{'turn%':>9}{'lift':>8}")
        for itv in brate.index:
            lift = brate[itv] / base if base > 0 else 0
            txt = f"[{itv.left:.1f}, {itv.right:.1f}]"
            print(f"  {txt:<26}{bn[itv]:>10,}{brate[itv]:>8.2f}%{lift:>7.1f}x")
        print(f"  (base rate among cap-known rows: {base:.2f}%)")


if __name__ == "__main__":
    main()
