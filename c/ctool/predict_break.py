#!/usr/bin/env python3
"""
predict_break.py — STEP 4: apply the trained scorer live.

For a given date it computes the SAME 20-day features used in training (r0..r19 daily
return %, vr0..vr19 volume / 20d-avg, mcap_b) for every stock, loads break_scorer.joblib,
and prints the stocks scoring highest (0-100). This is the payoff: buyable candidates,
not just an AUC.

Features here are computed with NO forward data (we score "today"), but with the exact
same formulas as build_turn_dataset.build_one, so live scores match training.

Usage:
    python predict_break.py                       # latest trading day, score >= 60
    python predict_break.py --date 2026-05-20
    python predict_break.py --min-score 50 --top 30
    python predict_break.py -q                    # save file only

Output: share_data/predict_<date>.txt
"""
import os
import csv
import sys
import argparse

import numpy as np
import pandas as pd
import joblib

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
META_PATH = os.path.join(TOOL_DIR, "share_data", "stock_meta.csv")
SHARE_DIR = os.path.join(TOOL_DIR, "share_data")
MODEL_PATH = os.path.join(SHARE_DIR, "break_scorer.joblib")
CAL_FILE = os.path.join(HISTORY_DIR, "000001.csv")

BEFORE = 20


def load_meta():
    """code -> (name, industry, float_mcap_yuan)."""
    out = {}
    if not os.path.exists(META_PATH):
        return out
    with open(META_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row.get("code", "").strip()
            try:
                mc = float(row.get("float_mcap_now", "0") or "0")
            except ValueError:
                mc = np.nan
            out[code] = (row.get("name", "").strip(), row.get("industry", "").strip(), mc)
    return out


def features_at(df, mcap, target_ts, feat_cols):
    """20-day feature vector at target_ts, same formulas as build_one. None if not scorable."""
    df = df.sort_values("Date").reset_index(drop=True)
    idx = df.index[df["Date"] == target_ts]
    if len(idx) == 0:
        return None
    i = int(idx[0])
    if i < BEFORE:                      # need 20 returns -> >= 20 prior rows
        return None
    c, v = df["Close"], df["Volume"]
    ret = (c / c.shift(1) - 1) * 100
    vavg = v.iloc[i - (BEFORE - 1): i + 1].mean()
    if not np.isfinite(vavg) or vavg == 0:
        return None
    feat = {}
    for k in range(BEFORE):
        j = i - (BEFORE - 1 - k)        # r0 oldest ... r19 = day i
        feat[f"r{k}"] = float(ret.iloc[j])
        feat[f"vr{k}"] = float(v.iloc[j] / vavg)
    feat["mcap_b"] = (mcap / 1e9) if (mcap and np.isfinite(mcap)) else np.nan
    row = []
    for name in feat_cols:
        val = feat.get(name, np.nan)
        if name != "mcap_b" and not np.isfinite(val):
            return None                 # missing price/vol feature -> skip stock
        row.append(val)
    return np.array(row, dtype=float), float(c.iloc[i]), float(ret.iloc[i])


def main():
    ap = argparse.ArgumentParser(description="Score stocks live with the trained break model")
    ap.add_argument("--date", help="trading day YYYY-MM-DD (default: latest)")
    ap.add_argument("--min-score", type=float, default=60.0, help="show stocks scoring >= this (default 60)")
    ap.add_argument("--top", type=int, default=None, help="cap the list to N best (optional)")
    ap.add_argument("-q", "--quiet", action="store_true", help="save file only, no stdout")
    args = ap.parse_args()

    if not os.path.exists(MODEL_PATH):
        sys.exit(f"no model at {MODEL_PATH} — run train_break.py first")
    bundle = joblib.load(MODEL_PATH)
    model, feat_cols = bundle["model"], bundle["feat_cols"]
    mcap_med = bundle.get("mcap_median", np.nan)

    cal = pd.read_csv(CAL_FILE, parse_dates=["Date"])
    target = pd.Timestamp(args.date) if args.date else cal["Date"].max()

    meta = load_meta()
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))

    recs = []
    scored = 0
    for fn in files:
        code = fn[:-4]
        name, industry, mc = meta.get(code, ("", "", np.nan))
        try:
            df = pd.read_csv(os.path.join(HISTORY_DIR, fn), parse_dates=["Date"])
        except Exception:
            continue
        if df.empty or "Close" not in df.columns:
            continue
        res = features_at(df, mc, target, feat_cols)
        if res is None:
            continue
        x, close, day_ret = res
        if not np.isfinite(x[feat_cols.index("mcap_b")]):
            x[feat_cols.index("mcap_b")] = mcap_med
        score = float(model.predict_proba(x.reshape(1, -1))[0, 1] * 100)
        scored += 1
        recs.append((score, code, name, industry, close, day_ret,
                     (mc / 1e9) if np.isfinite(mc) else np.nan))

    recs.sort(key=lambda r: r[0], reverse=True)
    hits = [r for r in recs if r[0] >= args.min_score]
    if args.top:
        hits = hits[: args.top]

    lines = []
    lines.append(f"predict_break — {target.date()}  (model: {bundle.get('kind','?')}, "
                 f"break>{bundle.get('high',60):.0f})")
    lines.append(f"stocks scored: {scored}   scoring >= {args.min_score:.0f}: {len(hits)}")
    lines.append("=" * 78)
    lines.append(f"{'#':>3} {'score':>6} {'code':<7} {'name':<10} {'close':>8} {'day%':>7} {'mcapB':>7}  industry")
    lines.append("-" * 78)
    show = hits if hits else recs[:10]
    if not hits:
        lines.append("(none cleared the threshold — showing top 10 for context)")
    for rank, (sc, code, name, industry, close, day_ret, mcb) in enumerate(show, 1):
        mcs = f"{mcb:6.1f}" if np.isfinite(mcb) else "   n/a"
        lines.append(f"{rank:>3} {sc:6.1f} {code:<7} {name[:10]:<10} {close:8.2f} {day_ret:7.1f} {mcs}  {industry[:18]}")
    report = "\n".join(lines)

    os.makedirs(SHARE_DIR, exist_ok=True)
    out_path = os.path.join(SHARE_DIR, f"predict_{target.date()}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    if not args.quiet:
        print(report)
    print(f"\nsaved -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
