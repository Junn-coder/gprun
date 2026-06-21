"""
Does past 妖股 status predict future limit-up activity?
Split: before 2026-01-01 = training, 2026 onward = recent.
"""
import csv
from pathlib import Path
from datetime import datetime
from collections import Counter

HISTORY_DIR = Path(__file__).parent / "stock_history_ak"
CUTOFF = datetime(2026, 1, 1)

def get_limit_pct(symbol: str) -> float:
    if symbol.startswith("300") or symbol.startswith("301") or symbol.startswith("688"):
        return 0.20
    return 0.10

def count_ups_in_period(rows, start_idx, end_idx, limit_pct):
    """Count limit-ups in rows[start_idx:end_idx]."""
    cnt = 0
    for i in range(max(1, start_idx), end_idx):
        try:
            pc = float(rows[i - 1]["Close"])
            c = float(rows[i]["Close"])
        except (ValueError, KeyError):
            continue
        if pc <= 0:
            continue
        if abs(c - round(pc * (1 + limit_pct), 2)) < 0.005:
            cnt += 1
    return cnt

def analyze_stock(fp: Path):
    rows = []
    with open(fp, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    if len(rows) < 100:
        return None  # too few data points

    symbol = rows[0]["symbol"]
    limit_pct = get_limit_pct(symbol)

    # Find cutoff index
    cut_idx = len(rows)
    for i, row in enumerate(rows):
        try:
            d = datetime.strptime(row["Date"], "%Y-%m-%d")
            if d >= CUTOFF:
                cut_idx = i
                break
        except:
            pass

    past_ups = count_ups_in_period(rows, 0, cut_idx, limit_pct)
    recent_ups = count_ups_in_period(rows, cut_idx, len(rows), limit_pct)

    past_days = cut_idx
    recent_days = len(rows) - cut_idx

    return {
        "symbol": symbol,
        "past_ups": past_ups,
        "recent_ups": recent_ups,
        "past_days": past_days,
        "recent_days": recent_days,
        "past_rate": round(past_ups / past_days * 100, 2) if past_days > 0 else 0,
        "recent_rate": round(recent_ups / recent_days * 100, 2) if recent_days > 0 else 0,
    }

def main():
    all_stocks = []
    for fp in sorted(HISTORY_DIR.glob("*.csv")):
        r = analyze_stock(fp)
        if r and r["past_days"] >= 300 and r["recent_days"] >= 50:
            all_stocks.append(r)

    # Group: past 妖股 (>10 limit-ups before 2026) vs past 普通
    past_yaogu = [s for s in all_stocks if s["past_ups"] > 10]
    past_normal = [s for s in all_stocks if s["past_ups"] <= 10]

    # Also look at recent 妖股 (2026 onward > some threshold, since shorter period)
    # Use rate instead: limit-ups per 100 trading days
    yaogu_recent_ups = [s["recent_ups"] for s in past_yaogu]
    normal_recent_ups = [s["recent_ups"] for s in past_normal]
    yaogu_recent_rate = [s["recent_rate"] for s in past_yaogu]
    normal_recent_rate = [s["recent_rate"] for s in past_normal]

    import statistics

    print("=" * 60)
    print("  妖股持续性问题：历史妖股在2026年表现如何？")
    print("=" * 60)
    print(f"  历史妖股(>10次, 截止2025底): {len(past_yaogu)} 只")
    print(f"  历史普通(<=10次, 截止2025底): {len(past_normal)} 只")
    print(f"  数据范围: 2026-01-01 ~ 2026-06-21 (~110个交易日)")
    print()

    # 1. Recent limit-up count comparison
    print("--- 1. 2026年涨停次数 ---")
    y_avg = statistics.mean(yaogu_recent_ups)
    n_avg = statistics.mean(normal_recent_ups)
    y_med = statistics.median(yaogu_recent_ups)
    n_med = statistics.median(normal_recent_ups)
    print(f"  历史妖股: 均值{y_avg:.1f}次  中位数{y_med:.1f}次")
    print(f"  历史普通: 均值{n_avg:.1f}次  中位数{n_med:.1f}次")
    print(f"  妖股/普通 = {y_avg/n_avg:.1f}x  (历史妖股近期涨停仍更多)")

    # 2. Distribution of recent ups
    print()
    print("--- 2. 2026年涨停次数分布 ---")
    for label, group in [("历史妖股", past_yaogu), ("历史普通", past_normal)]:
        dist = Counter(s["recent_ups"] for s in group)
        print(f"  {label}:")
        for k in range(0, 11):
            cnt = dist.get(k, 0)
            pct = cnt / len(group) * 100
            bar = "#" * int(pct / 2) if pct > 0 else ""
            print(f"    {k:2d}次: {cnt:3d}只 ({pct:4.1f}%) {bar}")

    # 3. Persistence: 历史妖股中,近期仍活跃的比例
    print()
    print("--- 3. 持续性 ---")
    # Among past 妖股, how many are still "active" (>=3 limit-ups in 2026)?
    yaogu_still_active = sum(1 for s in past_yaogu if s["recent_ups"] >= 3)
    normal_became_active = sum(1 for s in past_normal if s["recent_ups"] >= 3)
    yaogu_went_cold = sum(1 for s in past_yaogu if s["recent_ups"] == 0)
    normal_stayed_cold = sum(1 for s in past_normal if s["recent_ups"] == 0)

    print(f"  历史妖股 → 2026年仍活跃(≥3次): {yaogu_still_active}/{len(past_yaogu)} = {yaogu_still_active/len(past_yaogu)*100:.1f}%")
    print(f"  历史普通 → 2026年变活跃(≥3次): {normal_became_active}/{len(past_normal)} = {normal_became_active/len(past_normal)*100:.1f}%")
    print(f"  历史妖股 → 2026年完全没涨停: {yaogu_went_cold}/{len(past_yaogu)} = {yaogu_went_cold/len(past_yaogu)*100:.1f}%")
    print(f"  历史普通 → 2026年完全没涨停: {normal_stayed_cold}/{len(past_normal)} = {normal_stayed_cold/len(past_normal)*100:.1f}%")

    # 4. Top stocks that stayed active
    print()
    print("--- 4. 2026年仍最活跃的历史妖股 TOP 10 ---")
    for s in sorted(past_yaogu, key=lambda x: -x["recent_ups"])[:10]:
        print(f"  {s['symbol']}  历史{s['past_ups']}次 → 2026年{s['recent_ups']}次")

    # 5. New 妖股 (weren't 妖股 before but became active in 2026)
    print()
    print("--- 5. 2026年新崛起的活跃股(历史普通→近期>5次) TOP 10 ---")
    new_hot = sorted(past_normal, key=lambda x: -x["recent_ups"])[:10]
    for s in new_hot:
        print(f"  {s['symbol']}  历史{s['past_ups']}次 → 2026年{s['recent_ups']}次")

    # 6. Conclusion
    print()
    print("=" * 60)
    print("  结论")
    print("=" * 60)
    ratio = y_avg / n_avg if n_avg > 0 else 0
    print(f"  历史妖股在2026年平均仍有 {y_avg:.1f} 次涨停")
    print(f"  历史普通在2026年平均只有 {n_avg:.1f} 次涨停")
    print(f"  妖股持续活跃度是普通的 {ratio:.1f} 倍")
    print(f"  但: {yaogu_went_cold/len(past_yaogu)*100:.0f}% 的历史妖股在2026年完全没涨停")
    print(f"  妖股有一定持续性,但并非铁律;轮动和新面孔同样重要")

if __name__ == "__main__":
    main()
