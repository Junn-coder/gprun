#!/usr/bin/env python3
"""
单只股票查询脚本 v3
一次查一只，最稳定，永不断连。

依赖: pip install akshare

用法:
    python stock_one.py 300308          # 查 A 股
    python stock_one.py 688008
    python stock_one.py NVDA            # 查美股
    python stock_one.py AMD

批量查询（顺序执行，每只间隔 2 秒，避免风控）:
    python stock_one.py 300308 688008 603986 NVDA AMD AVGO

导出 JSON 给 Claude 分析:
    python stock_one.py 300308 688008 603986 --export
"""

import sys
import json
import time
from datetime import datetime
import akshare as ak


# 你想跟踪股票的中文名映射（可选，只是为了打印好看）
NAMES = {
    "300308": "中际旭创",
    "688008": "澜起科技",
    "603986": "兆易创新",
    "688256": "寒武纪",
    "300782": "卓胜微",
    "603501": "韦尔股份",
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "TSM": "TSMC",
    "MU": "Micron",
}


def is_a_stock(code):
    """判断是不是 A 股代码（6 位数字）"""
    return code.isdigit() and len(code) == 6


def query_a_stock(code):
    """查询单只 A 股"""
    name = NAMES.get(code, "")
    df = ak.stock_bid_ask_em(symbol=code)
    data = dict(zip(df['item'], df['value']))

    # akshare 返回的 名称 字段就是股票名
    if not name:
        name = str(data.get('股票名称', code))

    return {
        "type": "A股",
        "code": code,
        "name": name,
        "price": float(data.get('最新', 0)),
        "change_pct": float(data.get('涨幅', 0)),
        "change_amt": float(data.get('涨跌', 0)),
        "open": float(data.get('今开', 0)),
        "high": float(data.get('最高', 0)),
        "low": float(data.get('最低', 0)),
        "prev_close": float(data.get('昨收', 0)),
        "amount": float(data.get('金额', 0)),
        "turnover_rate": float(data.get('换手', 0)),
        "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def query_us_stock(ticker):
    """查询单只美股 — 用历史日线接口拿最近 2 天，算出当日涨跌"""
    name = NAMES.get(ticker.upper(), "")
    df = ak.stock_us_daily(symbol=ticker.upper(), adjust="")
    if df is None or df.empty:
        raise ValueError(f"No data for {ticker}")

    df = df.tail(2)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    change_pct = (last['close'] - prev['close']) / prev['close'] * 100

    # 日期字段在不同 akshare 版本可能是 index 或者 column
    date_str = ""
    if 'date' in last.index:
        date_str = str(last['date'])
    else:
        try:
            date_str = str(df.index[-1])[:10]
        except Exception:
            date_str = ""

    return {
        "type": "美股",
        "ticker": ticker.upper(),
        "name": name,
        "price": float(last['close']),
        "change_pct": float(change_pct),
        "open": float(last['open']),
        "high": float(last['high']),
        "low": float(last['low']),
        "prev_close": float(prev['close']),
        "volume": float(last['volume']),
        "date": date_str,
        "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def print_one(data):
    """打印单只股票"""
    if data['type'] == 'A股':
        sym = "↑" if data['change_pct'] > 0 else ("↓" if data['change_pct'] < 0 else "—")
        amt = data['amount']
        amt_str = f"{amt/1e8:.2f}亿" if amt >= 1e8 else (f"{amt/1e4:.0f}万" if amt > 0 else "—")
        print(f"\n[{data['type']}] {data['code']} {data['name']}")
        print(f"  最新价: {data['price']:.2f} 元   {sym} {data['change_pct']:+.2f}%  ({data['change_amt']:+.2f})")
        print(f"  开:{data['open']:.2f}  高:{data['high']:.2f}  低:{data['low']:.2f}  昨收:{data['prev_close']:.2f}")
        print(f"  成交额: {amt_str}   换手率: {data['turnover_rate']:.2f}%")
        print(f"  查询时间: {data['query_time']}")
    else:
        sym = "↑" if data['change_pct'] > 0 else ("↓" if data['change_pct'] < 0 else "—")
        print(f"\n[{data['type']}] {data['ticker']} {data['name']}")
        print(f"  收盘价: ${data['price']:.2f}   {sym} {data['change_pct']:+.2f}%")
        print(f"  开:{data['open']:.2f}  高:{data['high']:.2f}  低:{data['low']:.2f}  昨收:{data['prev_close']:.2f}")
        print(f"  数据日期: {data['date']}")
        print(f"  查询时间: {data['query_time']}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    export = False
    if "--export" in args:
        export = True
        args.remove("--export")

    if not args:
        print("请至少给一个股票代码")
        return

    results = []
    for i, code in enumerate(args):
        # 多只之间间隔，避免被风控
        if i > 0:
            time.sleep(2)

        try:
            if is_a_stock(code):
                data = query_a_stock(code)
            else:
                data = query_us_stock(code)
            results.append(data)
            print_one(data)
        except Exception as e:
            err = {"code": code, "ok": False, "error": str(e)}
            results.append(err)
            print(f"\n❌ {code} 查询失败: {e}")

    if export:
        filename = f"stocks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已导出 {filename}（粘贴给 Claude 做分析）")


if __name__ == "__main__":
    main()
