"""
Profile "妖股" (stocks with >10 limit-ups) vs normal stocks.
Analyzes: board type, price range, volatility, volume characteristics.
"""
import csv
import statistics
from pathlib import Path
from collections import Counter

HISTORY_DIR = Path(__file__).parent / "stock_history_ak"

def get_limit_pct(symbol: str) -> float:
    if symbol.startswith("300") or symbol.startswith("301") or symbol.startswith("688"):
        return 0.20
    return 0.10

def get_board(symbol: str) -> str:
    if symbol.startswith("300") or symbol.startswith("301"):
        return "创业板"
    if symbol.startswith("688"):
        return "科创板"
    if symbol.startswith("60"):
        return "沪主板"
    if symbol.startswith("00") or symbol.startswith("001") or symbol.startswith("002") or symbol.startswith("003"):
        return "深主板"
    return "其他"

def analyze_stock(fp: Path):
    rows = []
    with open(fp, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    if len(rows) < 10:
        return None

    symbol = rows[0]["symbol"]
    limit_pct = get_limit_pct(symbol)

    closes = []
    volumes = []
    returns = []
    up_count = 0

    for i, row in enumerate(rows):
        try:
            c = float(row["Close"])
            v = float(row["Volume"])
        except (ValueError, KeyError):
            continue
        closes.append(c)
        volumes.append(v)
        if i > 0:
            try:
                prev_c = float(rows[i-1]["Close"])
                if prev_c > 0:
                    ret = (c - prev_c) / prev_c
                    returns.append(ret)
                # Check limit-up
                if abs(c - round(prev_c * (1 + limit_pct), 2)) < 0.005:
                    up_count += 1
            except:
                pass

    if not closes:
        return None

    avg_close = statistics.mean(closes)
    avg_vol = statistics.mean(volumes) if volumes else 0
    med_vol = statistics.median(volumes) if volumes else 0
    vol_spike_ratio = max(volumes) / med_vol if med_vol > 0 else 0
    std_ret = statistics.stdev(returns) * 100 if len(returns) >= 2 else 0  # daily volatility %
    price_range = max(closes) - min(closes)
    price_volatility = (price_range / avg_close * 100) if avg_close > 0 else 0  # range/avg %
    price_low = min(closes)
    price_high = max(closes)
    days = len(closes)
    up_ratio = up_count / days * 100

    return {
        "symbol": symbol,
        "board": get_board(symbol),
        "up_count": up_count,
        "up_ratio": round(up_ratio, 2),
        "avg_close": round(avg_close, 2),
        "price_low": round(price_low, 2),
        "price_high": round(price_high, 2),
        "avg_vol": avg_vol,
        "vol_spike": round(vol_spike_ratio, 1),
        "std_ret_pct": round(std_ret, 2),
        "days": days,
    }

def main():
    all_analyzed = []
    for fp in sorted(HISTORY_DIR.glob("*.csv")):
        r = analyze_stock(fp)
        if r:
            all_analyzed.append(r)

    yaogu = [s for s in all_analyzed if s["up_count"] > 10]
    normal = [s for s in all_analyzed if s["up_count"] <= 10]

    def avg(vals):
        return round(statistics.mean(vals), 2)

    def med(vals):
        return round(statistics.median(vals), 2)

    print("=" * 60)
    print("  妖股特征分析 (涨停>10次 vs 普通股)")
    print("=" * 60)
    print(f"  妖股: {len(yaogu)} 只    普通股: {len(normal)} 只")
    print()

    # 1. Board distribution
    print("--- 1. 板块分布 ---")
    yb = Counter(s["board"] for s in yaogu)
    nb = Counter(s["board"] for s in normal)
    for board in ["深主板", "沪主板", "创业板", "科创板"]:
        yc = yb.get(board, 0)
        nc = nb.get(board, 0)
        yp = yc / len(yaogu) * 100
        np_ = nc / len(normal) * 100
        diff = yp - np_
        sign = "+" if diff > 0 else ""
        print(f"  {board:6s}: 妖股{yc:3}只({yp:4.1f}%)  普通{nc:3}只({np_:4.1f}%)  偏差{sign}{diff:.1f}%")

    # 2. Price level
    print()
    print("--- 2. 股价水平 ---")
    y_prices = [s["avg_close"] for s in yaogu]
    n_prices = [s["avg_close"] for s in normal]
    print(f"  妖股均价: 均值{avg(y_prices):.1f}  中位数{med(y_prices):.1f}")
    print(f"  普通均价: 均值{avg(n_prices):.1f}  中位数{med(n_prices):.1f}")

    # Price buckets
    print("  妖股价格分布:")
    for lo, hi, label in [(0,5,"<5"), (5,10,"5-10"), (10,20,"10-20"), (20,50,"20-50"), (50,200,"50-200"), (200,9999,"200+")]:
        yc = sum(1 for p in y_prices if lo <= p < hi)
        nc = sum(1 for p in n_prices if lo <= p < hi)
        print(f"    {label:>6s}: 妖{yc:3}({yc/len(yaogu)*100:4.1f}%)  普通{nc:3}({nc/len(normal)*100:4.1f}%)")

    # 3. Volatility
    print()
    print("--- 3. 日内波动率(日收益率标准差%) ---")
    y_vol = [s["std_ret_pct"] for s in yaogu if s["std_ret_pct"] > 0]
    n_vol = [s["std_ret_pct"] for s in normal if s["std_ret_pct"] > 0]
    print(f"  妖股: 均值{avg(y_vol):.2f}%  中位数{med(y_vol):.2f}%")
    print(f"  普通: 均值{avg(n_vol):.2f}%  中位数{med(n_vol):.2f}%")

    # 4. Volume spike ratio
    print()
    print("--- 4. 放量倍数(最大量/中位量) ---")
    y_spike = [s["vol_spike"] for s in yaogu if s["vol_spike"] > 0]
    n_spike = [s["vol_spike"] for s in normal if s["vol_spike"] > 0]
    print(f"  妖股: 均值{avg(y_spike):.1f}x  中位数{med(y_spike):.1f}x")
    print(f"  普通: 均值{avg(n_spike):.1f}x  中位数{med(n_spike):.1f}x")

    # 5. Average daily volume (log scale for readability)
    print()
    print("--- 5. 日均成交量(手) ---")
    y_av = [s["avg_vol"] for s in yaogu]
    n_av = [s["avg_vol"] for s in normal]
    print(f"  妖股: 均值{avg(y_av):.0f}  中位数{med(y_av):.0f}")
    print(f"  普通: 均值{avg(n_av):.0f}  中位数{med(n_av):.0f}")

    # 6. Top 妖股 by different dimensions
    print()
    print("--- 6. 妖股极端值 TOP 5 ---")
    print("  最高波动率:")
    for s in sorted(yaogu, key=lambda x: -x["std_ret_pct"])[:5]:
        print(f"    {s['symbol']}  {s['std_ret_pct']:.2f}%  {s['board']}  涨停{s['up_count']}次")

    print("  最大放量倍数:")
    for s in sorted(yaogu, key=lambda x: -x["vol_spike"])[:5]:
        print(f"    {s['symbol']}  {s['vol_spike']:.1f}x  {s['board']}  涨停{s['up_count']}次")

    # 7. Conclusion
    print()
    print("=" * 60)
    print("  总结: 妖股 vs 普通股的显著差异")
    print("=" * 60)
    print(f"  1. 板块偏好: 深主板占比最高,创业板/科创板偏少")
    print(f"  2. 股价区间: 妖股集中在低价区(<20元)")
    print(f"  3. 波动率: 妖股是普通的 {avg(y_vol)/avg(n_vol):.1f}x")
    print(f"  4. 放量倍数: 妖股 {avg(y_spike)/avg(n_spike):.1f}x 于普通")

if __name__ == "__main__":
    main()
