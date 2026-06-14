#!/usr/bin/env python3
"""
单只股票查询脚本 v4
彻底解决 A 股断连：多数据源切换 + 智能重试 + 自动降级

依赖: pip install akshare

用法:
    python stock_one.py 300308          # 查 A 股
    python stock_one.py 688008
    python stock_one.py NVDA            # 查美股

批量查询（依次执行，每只间隔 5 秒）:
    python stock_one.py 300308 688008 603986 NVDA AMD AVGO

导出 JSON 给 Claude 分析:
    python stock_one.py 300308 688008 603986 --export
"""

import sys
import json
import time
from datetime import datetime
import akshare as ak


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
    return code.isdigit() and len(code) == 6


# ============================================================
# A 股查询：3 个数据源轮流尝试
# ============================================================

def query_a_via_bidask(code):
    """方法1: 东方财富盘口（轻量，最快）"""
    df = ak.stock_bid_ask_em(symbol=code)
    data = dict(zip(df['item'], df['value']))
    return {
        "price": float(data.get('最新', 0)),
        "change_pct": float(data.get('涨幅', 0)),
        "change_amt": float(data.get('涨跌', 0)),
        "open": float(data.get('今开', 0)),
        "high": float(data.get('最高', 0)),
        "low": float(data.get('最低', 0)),
        "prev_close": float(data.get('昨收', 0)),
        "amount": float(data.get('金额', 0)),
        "turnover_rate": float(data.get('换手', 0)),
        "source": "东方财富(盘口)",
    }


def query_a_via_sina_hist(code):
    """方法2: 新浪财经历史日线（绕开东方财富）"""
    # 新浪格式: sh603986 / sz300308 / sh688008
    if code.startswith('6'):
        sina_code = f"sh{code}"
    else:
        sina_code = f"sz{code}"

    df = ak.stock_zh_a_daily(symbol=sina_code, adjust="")
    if df is None or df.empty:
        raise ValueError("Sina returned empty")
    df = df.tail(2)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    change_amt = last['close'] - prev['close']
    change_pct = change_amt / prev['close'] * 100

    return {
        "price": float(last['close']),
        "change_pct": float(change_pct),
        "change_amt": float(change_amt),
        "open": float(last['open']),
        "high": float(last['high']),
        "low": float(last['low']),
        "prev_close": float(prev['close']),
        "amount": float(last.get('amount', 0) or last.get('volume', 0)),
        "turnover_rate": 0,
        "source": "新浪财经(历史)",
    }


def query_a_via_tx(code):
    """方法3: 腾讯财经分钟线（最后备份）"""
    # 腾讯格式: sh603986 / sz300308
    if code.startswith('6'):
        tx_code = f"sh{code}"
    else:
        tx_code = f"sz{code}"

    df = ak.stock_zh_a_minute(symbol=tx_code, period='1', adjust='')
    if df is None or df.empty:
        raise ValueError("Tencent returned empty")
    last = df.iloc[-1]
    return {
        "price": float(last['close']),
        "change_pct": 0,  # 分钟数据没有涨跌幅，需要另外算
        "change_amt": 0,
        "open": float(last['open']),
        "high": float(last['high']),
        "low": float(last['low']),
        "prev_close": 0,
        "amount": 0,
        "turnover_rate": 0,
        "source": "腾讯财经(分钟)",
    }


def query_a_stock(code):
    """A股查询：3个数据源依次尝试，每次失败等3秒"""
    name = NAMES.get(code, code)
    methods = [
        ("东方财富", query_a_via_bidask),
        ("新浪财经", query_a_via_sina_hist),
        ("腾讯财经", query_a_via_tx),
    ]

    last_error = None
    for src_name, method in methods:
        for attempt in range(2):  # 每个源最多重试 2 次
            try:
                data = method(code)
                data.update({
                    "type": "A股",
                    "code": code,
                    "name": name,
                    "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                return data
            except Exception as e:
                last_error = f"{src_name}: {e}"
                if attempt == 0:
                    time.sleep(3)  # 失败后等 3 秒再试一次
        # 一个数据源彻底失败，切换下一个之前等 2 秒
        time.sleep(2)

    raise RuntimeError(f"All 3 data sources failed. Last error: {last_error}")


# ============================================================
# 美股查询
# ============================================================

def query_us_stock(ticker):
    name = NAMES.get(ticker.upper(), ticker.upper())
    df = ak.stock_us_daily(symbol=ticker.upper(), adjust="")
    if df is None or df.empty:
        raise ValueError(f"No data for {ticker}")
    df = df.tail(2)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    change_pct = (last['close'] - prev['close']) / prev['close'] * 100

    date_str = ""
    if 'date' in last.index:
        date_str = str(last['date'])[:10]
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
        "source": "新浪财经(美股)",
    }


# ============================================================
# 输出
# ============================================================

def print_one(data):
    if data['type'] == 'A股':
        sym = "↑" if data['change_pct'] > 0 else ("↓" if data['change_pct'] < 0 else "—")
        amt = data.get('amount', 0)
        amt_str = f"{amt/1e8:.2f}亿" if amt >= 1e8 else (f"{amt/1e4:.0f}万" if amt > 0 else "—")
        print(f"\n[{data['type']}] {data['code']} {data['name']}  (源: {data['source']})")
        print(f"  最新价: {data['price']:.2f} 元   {sym} {data['change_pct']:+.2f}%  ({data['change_amt']:+.2f})")
        print(f"  开:{data['open']:.2f}  高:{data['high']:.2f}  低:{data['low']:.2f}  昨收:{data['prev_close']:.2f}")
        if amt > 0:
            print(f"  成交额: {amt_str}   换手率: {data.get('turnover_rate', 0):.2f}%")
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
        # 多只查询之间间隔 5 秒，避免被风控
        if i > 0:
            print(f"\n(等待 5 秒避免请求过频...)")
            time.sleep(5)

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
        print(f"\n✅ 已导出 {filename}")
        print(f"   把这个文件内容贴给 Claude 做分析。")


if __name__ == "__main__":
    main()
