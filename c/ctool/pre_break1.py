#!/usr/bin/env python3
"""
pre_break1.py — pre_break, MODEL EDITION.

Same job as pre_break.py (pick today's pre-breakout candidates) but the brain is the
trained model (break_scorer.joblib) instead of the hand-written momentum gates. This is a
SEPARATE file on purpose: the original pre_break.py is left untouched as the baseline.

It scores every stock for the target day with the same 20-day features used in training
(reused from predict_break.py — no look-ahead) and prints the TOP-N by score.

CAUTION: the out-of-sample test (train 2025 -> test 2026) showed only a thin, lottery-driven
edge (median pick ~flat). Treat this list as IDEAS to inspect, not buy signals.

Usage:
    python pre_break1.py                 # latest day, top 6
    python pre_break1.py --top 4
    python pre_break1.py --date 2026-05-20
Output: share_data/prebreak1_<date>.txt
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd
import joblib

from predict_break import HISTORY_DIR, SHARE_DIR, MODEL_PATH, CAL_FILE, load_meta, features_at


def main():
    ap = argparse.ArgumentParser(description="pre_break, model edition — today's picks")
    ap.add_argument("--date", help="trading day YYYY-MM-DD (default: latest)")
    ap.add_argument("--top", type=int, default=6, help="how many candidates (default 6)")
    ap.add_argument("--model", default=MODEL_PATH, help="model path")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    bundle = joblib.load(args.model)
    model, feat_cols = bundle["model"], bundle["feat_cols"]
    mcap_med = bundle.get("mcap_median", np.nan)
    mi = feat_cols.index("mcap_b")

    cal = pd.read_csv(CAL_FILE, parse_dates=["Date"])
    target = pd.Timestamp(args.date) if args.date else cal["Date"].max()
    meta = load_meta()
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".csv"))

    recs = []
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
        if not np.isfinite(x[mi]):
            x[mi] = mcap_med
        score = float(model.predict_proba(x.reshape(1, -1))[0, 1] * 100)
        recs.append((score, code, name, industry, close, day_ret,
                     (mc / 1e9) if np.isfinite(mc) else np.nan))

    recs.sort(key=lambda r: r[0], reverse=True)
    top = recs[: args.top]

    lines = []
    lines.append(f"pre_break1 (MODEL edition) — {target.date()}   top {args.top} of {len(recs)} scored")
    lines.append(f"model: break_scorer ({bundle.get('kind','?')}, target +20%/10d)")
    lines.append("CAUTION: out-of-sample edge is thin & lottery-driven — IDEAS, not signals.")
    lines.append("=" * 76)
    lines.append(f"{'#':>2} {'score':>6} {'code':<7} {'name':<10} {'close':>8} {'day%':>6} {'mcapB':>7}  industry")
    lines.append("-" * 76)
    for rank, (sc, code, name, industry, close, day_ret, mcb) in enumerate(top, 1):
        mcs = f"{mcb:6.1f}" if np.isfinite(mcb) else "   n/a"
        lines.append(f"{rank:>2} {sc:6.1f} {code:<7} {name[:10]:<10} {close:8.2f} {day_ret:6.1f} {mcs}  {industry[:18]}")
    lines.append("")
    lines.append("day% = the stock's return on the pick day. Mostly negative => the model is")
    lines.append("buying recent dips (mean-reversion), betting on a bounce. Inspect each before acting.")
    report = "\n".join(lines)

    os.makedirs(SHARE_DIR, exist_ok=True)
    out = os.path.join(SHARE_DIR, f"prebreak1_{target.date()}.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    if not args.quiet:
        print(report)
    print(f"\nsaved -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
