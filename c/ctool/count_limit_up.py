"""
Count 涨停 (limit-up) days for all stocks in stock_history_ak/.
Output: c/up.md

A-share limit rules:
- Main board (000xxx-003xxx, 600xxx-605xxx): 10%
- ChiNext/创业板 (300xxx-301xxx): 20%
- STAR/科创板 (688xxx): 20%
"""
import csv
import os
from pathlib import Path

HISTORY_DIR = Path(__file__).parent / "stock_history_ak"
OUTPUT = Path(__file__).parent.parent / "up.md"  # c/up.md

def get_limit_pct(symbol: str) -> float:
    """Return limit-up percentage for the stock."""
    if symbol.startswith("300") or symbol.startswith("301"):
        return 0.20
    if symbol.startswith("688"):
        return 0.20
    return 0.10

def count_limit_ups(csv_path: Path) -> tuple[str, int]:
    """Return (symbol, limit_up_count) for a stock CSV."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if len(rows) < 2:
        symbol = rows[0]["symbol"] if rows else csv_path.stem
        return (symbol, 0)

    symbol = rows[0]["symbol"]
    limit_pct = get_limit_pct(symbol)
    count = 0

    for i in range(1, len(rows)):
        try:
            prev_close = float(rows[i - 1]["Close"])
            close = float(rows[i]["Close"])
        except (ValueError, KeyError):
            continue
        if prev_close <= 0:
            continue
        limit_price = round(prev_close * (1 + limit_pct), 2)
        # Allow small rounding tolerance
        if abs(close - limit_price) < 0.005:
            count += 1

    return (symbol, count)

def main():
    csv_files = sorted(HISTORY_DIR.glob("*.csv"))
    results = []

    for fp in csv_files:
        symbol, count = count_limit_ups(fp)
        results.append((symbol, count))

    # Filter to only stocks with >=1 limit-up
    with_limit_up = [(s, c) for s, c in results if c > 0]
    with_limit_up.sort(key=lambda x: -x[1])  # descending by count

    total_stocks = len(results)
    total_with_up = len(with_limit_up)
    total_up_events = sum(c for _, c in with_limit_up)

    lines = []
    lines.append("# 涨停统计 (Limit-Up Analysis)")
    lines.append(f"")
    lines.append(f"**数据日期**: 2026-06-21")
    lines.append(f"**股票总数**: {total_stocks}")
    lines.append(f"**有涨停的股票数**: {total_with_up}")
    lines.append(f"**涨停总次数**: {total_up_events}")
    lines.append(f"")
    lines.append(f"## 涨停次数排名")
    lines.append(f"")
    lines.append(f"| 排名 | 股票代码 | 涨停次数 |")
    lines.append(f"|------|----------|----------|")

    for rank, (sym, cnt) in enumerate(with_limit_up, 1):
        lines.append(f"| {rank} | {sym} | {cnt} |")

    lines.append(f"")
    lines.append(f"## 无涨停的股票 ({total_stocks - total_with_up} 只)")
    lines.append(f"")
    no_up = [s for s, c in results if c == 0]
    lines.append(", ".join(no_up))

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Done. Output: {OUTPUT}")
    print(f"Total stocks: {total_stocks}")
    print(f"Stocks with limit-up: {total_with_up}")
    print(f"Total limit-up events: {total_up_events}")

if __name__ == "__main__":
    main()
