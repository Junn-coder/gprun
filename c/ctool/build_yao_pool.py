#!/usr/bin/env python3
"""
build_yao_pool.py — dynamic yao pool builder from flat_yao data.

Scans all flat_yao CSVs, ranks stocks by recent limit-up frequency,
applies cap/price/data-quality filters, and outputs a fresh yaolist.md.

Replaces the static 25-stock snapshot with a data-driven monthly refresh.

Usage:
    python build_yao_pool.py                        # 6-month window, top 30
    python build_yao_pool.py --months 3 --top 25    # tighter window
    python build_yao_pool.py --top 50 --out /tmp    # dry-run to tmp
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FLAT_DIR = os.path.join(ROOT, "flat_yao")
META_CSV = os.path.join(HERE, "share_data", "stock_meta.csv")
YAOLIST_OUT = os.path.join(ROOT, "yao", "yaolist.md")

CAP_LOW = 30e8    # ¥3B float cap floor
CAP_HIGH = 500e8  # ¥50B float cap ceiling
PRICE_LOW = 3.0
PRICE_HIGH = 50.0
MIN_DAYS = 100     # minimum trading days in window


def load_meta():
    """Return dict: code -> {name, industry, float_mcap}."""
    if not os.path.exists(META_CSV):
        print("WARNING: stock_meta.csv not found — cap filter disabled")
        return {}
    df = pd.read_csv(META_CSV)
    meta = {}
    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        meta[code] = {
            "name": str(row.get("name", "")),
            "industry": str(row.get("industry", "")),
            "float_mcap": float(row.get("float_mcap_now", 0) or 0),
        }
    return meta


def count_limit_ups(df, start_date, end_date):
    """Count limit-up days in date range. Returns (count, last_close, avg_vol)."""
    df = df[(df["Date"] >= start_date) & (df["Date"] <= end_date)].copy()
    if len(df) < MIN_DAYS:
        return 0, 0, 0

    closes = []
    volumes = []
    lu = 0
    for i in range(1, len(df)):
        try:
            prev = float(df.iloc[i - 1]["Close"])
            curr = float(df.iloc[i]["Close"])
            vol = float(df.iloc[i]["Volume"])
        except (ValueError, KeyError):
            continue
        if prev <= 0:
            continue
        closes.append(curr)
        volumes.append(vol)
        chg = (curr - prev) / prev
        if 0.095 <= chg <= 0.105:
            lu += 1

    last_close = closes[-1] if closes else 0
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    return lu, last_close, avg_vol


def build_pool(months=6, top_n=30):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    meta = load_meta()
    files = sorted(f for f in os.listdir(FLAT_DIR) if f.endswith(".csv"))

    print(f"Scanning {len(files)} stocks: {start_date} → {end_date}")

    candidates = []
    skipped_short = 0
    skipped_cap = 0
    skipped_price = 0

    for fn in files:
        code = fn.replace(".csv", "")
        try:
            df = pd.read_csv(os.path.join(FLAT_DIR, fn))
            df["Date"] = df["Date"].astype(str)
        except Exception:
            continue

        lu, close, avg_vol = count_limit_ups(df, start_date, end_date)
        if lu == 0 and close == 0:
            skipped_short += 1
            continue

        # Price filter
        if close < PRICE_LOW or close > PRICE_HIGH:
            skipped_price += 1
            continue

        # Cap filter
        m = meta.get(code, {})
        fcap = m.get("float_mcap", 0)
        if fcap > 0 and (fcap < CAP_LOW or fcap > CAP_HIGH):
            skipped_cap += 1
            continue

        name = m.get("name", "")
        industry = m.get("industry", "")
        candidates.append({
            "code": code,
            "name": name if name else code,
            "industry": industry if industry else "-",
            "lu": lu,
            "close": close,
            "avg_vol": int(avg_vol),
            "float_mcap": fcap,
        })

    # Sort by LU count descending
    candidates.sort(key=lambda x: -x["lu"])
    pool = candidates[:top_n]

    print(f"  kept: {len(pool)}  skipped: short={skipped_short} cap={skipped_cap} price={skipped_price}")
    return pool, start_date, end_date


def write_yaolist(pool, start_date, end_date, out_path):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    lines.append(f"# 妖股池 — {len(pool)} stocks, daily pre-market scan\n")
    lines.append(f"**更新**: {today} | **窗口**: {start_date} → {end_date} | **规则**: 每日盘前扫描, 挑 2-3 只, 每只 ~¥5,000\n")
    lines.append(f"| # | 代码 | 名称 | 近价 | 涨停次 | 行业 | 流通市值(亿) |")
    lines.append(f"|---|------|------|------|--------|------|-------------|")

    for i, s in enumerate(pool, 1):
        name = s["name"] if s["name"] != s["code"] else s["code"]
        ind = s["industry"] if s["industry"] != "-" else "-"
        mcap_str = f"{s['float_mcap'] / 1e8:.0f}" if s["float_mcap"] > 0 else "-"
        lines.append(
            f"| {i} | {s['code']} | {name} | {s['close']:.2f} | {s['lu']} | {ind} | {mcap_str} |"
        )

    lines.append("")
    lines.append("---\n")
    lines.append("## 每日操作规则\n")
    lines.append("**盘前扫描 (8:30-9:00):**")
    lines.append("1. 跑 `python c/ctool/yao_gate.py` — 妖股专用闸门 (池内涨停数 + 指数极端崩盘检测)")
    lines.append("   - RED = 池内0涨停 或 任一指数跌 >3% → 今天不动")
    lines.append("   - AMBER = 池内1-2涨停 → 谨慎, 最多选 1 只")
    lines.append("   - GREEN = 池内3+涨停 → 进攻, 最多选 2 只")
    lines.append("2. 用 Sina API 拉所有现价 + 昨日是否涨停")
    lines.append("3. 筛选 2-3 只: 昨日涨停 + 首封早 (<10:00) + 换手适中 (5-20%)\n")
    lines.append("**入场:**")
    lines.append("- T+1 开盘价买入，每只 ¥5,000，向下取整 100 股")
    lines.append("- 若开盘一字板 (>+9.5%) → 放弃该票\n")
    lines.append("**出场 (先触发者执行):**")
    lines.append("- 3 个交易日无新涨停 → 次日开盘清仓")
    lines.append("- 收盘跌破入场价 -5% → 次日开盘清仓")
    lines.append("- 浮盈 +10% → 次日开盘清仓（吃完就走）")
    lines.append("- 炸板未回封 → 次日开盘清仓\n")
    lines.append("**仓位:**")
    lines.append("- 最多同时 2 只，每只 ¥5,000")
    lines.append("- 闸门 RED → 空仓")
    lines.append("")
    lines.append("> 池构建: `python c/ctool/build_yao_pool.py` — 基于 flat_yao 近6月涨停次数动态排序\n")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Backup existing
    if os.path.exists(out_path):
        backup = out_path.replace(".md", f".bak.{today}.md")
        os.rename(out_path, backup)
        print(f"  backup → {backup}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  wrote → {out_path}")


def main():
    ap = argparse.ArgumentParser(description="Build dynamic yao pool from flat_yao data")
    ap.add_argument("--months", type=int, default=6, help="lookback window in months (default 6)")
    ap.add_argument("--top", type=int, default=30, help="pool size (default 30)")
    ap.add_argument("--out", default=YAOLIST_OUT, help="output path (default c/yao/yaolist.md)")
    args = ap.parse_args()

    print(f"=== build_yao_pool: {args.months}mo window, top {args.top} ===")
    pool, start, end = build_pool(months=args.months, top_n=args.top)

    if not pool:
        print("ERROR: no candidates passed filters")
        sys.exit(1)

    print(f"\nTop {min(10, len(pool))} picks:")
    for s in pool[:10]:
        print(f"  {s['code']} {s['name']:<8s} LU={s['lu']:>2d}  ¥{s['close']:.2f}  {s['industry']}")

    write_yaolist(pool, start, end, args.out)

    # Print comparison to current pool
    old_codes = set()
    old_yaolist = os.path.join(ROOT, "yao", "yaolist.md")
    if os.path.exists(old_yaolist):
        with open(old_yaolist) as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 3:
                    c = parts[2].strip()
                    if c.isdigit() and len(c) == 6:
                        old_codes.add(c)
        new_codes = {s["code"] for s in pool}
        added = new_codes - old_codes
        dropped = old_codes - new_codes
        kept = new_codes & old_codes
        print(f"\nPool delta: +{len(added)} added  -{len(dropped)} dropped  ={len(kept)} kept")
        if dropped:
            print(f"  Dropped: {', '.join(sorted(dropped))}")
        if added:
            print(f"  Added:   {', '.join(sorted(added)[:10])}{'...' if len(added) > 10 else ''}")


if __name__ == "__main__":
    main()
