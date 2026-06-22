"""
Analyze whether 涨停 (limit-up) days are consecutive or phased.
"""
import csv
from pathlib import Path
from datetime import datetime
from collections import Counter

HISTORY_DIR = Path(__file__).parent / "stock_history_ak"

def get_limit_pct(symbol: str) -> float:
    if symbol.startswith("300") or symbol.startswith("301") or symbol.startswith("688"):
        return 0.20
    return 0.10

def analyze_stock(fp: Path) -> dict | None:
    rows = []
    with open(fp, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    if len(rows) < 2:
        return None

    symbol = rows[0]["symbol"]
    limit_pct = get_limit_pct(symbol)

    up_dates = []
    for i in range(1, len(rows)):
        try:
            prev_close = float(rows[i - 1]["Close"])
            close = float(rows[i]["Close"])
            date_str = rows[i]["Date"]
        except (ValueError, KeyError):
            continue
        if prev_close <= 0:
            continue
        limit_price = round(prev_close * (1 + limit_pct), 2)
        if abs(close - limit_price) < 0.005:
            up_dates.append(datetime.strptime(date_str, "%Y-%m-%d"))

    if not up_dates:
        return None

    total = len(up_dates)

    # Group consecutive runs (gap <= 3 calendar days = consecutive, handles weekends)
    runs = []
    run_start = up_dates[0]
    run_len = 1
    for j in range(1, len(up_dates)):
        gap = (up_dates[j] - up_dates[j - 1]).days
        if gap <= 3:
            run_len += 1
        else:
            runs.append((run_start.strftime("%Y-%m-%d"), run_len))
            run_start = up_dates[j]
            run_len = 1
    runs.append((run_start.strftime("%Y-%m-%d"), run_len))

    max_consecutive = max(r[1] for r in runs)
    num_phases = len(runs)
    avg_run = round(sum(r[1] for r in runs) / num_phases, 1)

    return {
        "symbol": symbol,
        "total": total,
        "max_consecutive": max_consecutive,
        "num_phases": num_phases,
        "avg_run": avg_run,
        "runs": runs,
    }

def main():
    all_stocks = []
    for fp in sorted(HISTORY_DIR.glob("*.csv")):
        r = analyze_stock(fp)
        if r and r["total"] > 10:
            all_stocks.append(r)

    all_stocks.sort(key=lambda x: -x["max_consecutive"])

    pure_consecutive = [s for s in all_stocks if s["num_phases"] == 1]
    mixed = [s for s in all_stocks if s["num_phases"] > 1]

    print("=== 涨停模式分析 (>10次涨停的股票) ===")
    print(f"总数: {len(all_stocks)}")
    print()
    print(f"纯连续涨停(仅1个阶段): {len(pure_consecutive)} 只")
    print(f"分阶段涨停(>=2阶段):   {len(mixed)} 只")
    print()

    print("--- 最长连续涨停 TOP 15 ---")
    for i, s in enumerate(all_stocks[:15], 1):
        tag = "纯连续" if s["num_phases"] == 1 else f"{s['num_phases']}阶段"
        print(f"  {i:2}. {s['symbol']}  总{s['total']}次  最长连续{s['max_consecutive']}天  {tag}")

    print()
    print("--- 阶段数分布 ---")
    dist = Counter(s["num_phases"] for s in all_stocks)
    for k in sorted(dist.keys()):
        bar = "#" * dist[k]
        print(f"  {k:2}阶段: {dist[k]:3} 只  {bar}")

    print()
    print("--- 最长连续天数分布 ---")
    dist2 = Counter(s["max_consecutive"] for s in all_stocks)
    for k in sorted(dist2.keys(), reverse=True)[:15]:
        print(f"  最长连续{k:2}天: {dist2[k]:3} 只")

    # Example: show a multi-phase stock
    print()
    print("--- 分阶段示例 (002640, 42次涨停) ---")
    for s in all_stocks:
        if s["symbol"] == "002640":
            for start, length in s["runs"]:
                print(f"  {start} 起连续 {length} 天")
            break

    # Overall pattern
    print()
    single_run_total = sum(s["total"] for s in pure_consecutive)
    multi_run_total = sum(s["total"] for s in mixed)
    print(f"纯连续涨停总次数: {single_run_total} (来自{len(pure_consecutive)}只)")
    print(f"分阶段涨停总次数: {multi_run_total} (来自{len(mixed)}只)")
    overall = single_run_total + multi_run_total
    print(f"分阶段占比: {multi_run_total}/{overall} = {multi_run_total/overall*100:.0f}%")

if __name__ == "__main__":
    main()
