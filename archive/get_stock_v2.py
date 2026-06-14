#!/usr/bin/env python3
"""
股票监控脚本 v2（轻量版）
修复了 v1 的断连问题，并支持导出 JSON 给 Claude 分析

依赖: pip install akshare pandas requests
使用:
    python stock_monitor_v2.py            # 屏幕显示
    python stock_monitor_v2.py --export   # 导出 stocks_data.json，可粘贴给 Claude
"""

import sys
import json
import time
from datetime import datetime
import akshare as ak


# ============================================================
# 配置区
# ============================================================
A_STOCKS = {
    "300308": "中际旭创",
    "688008": "澜起科技",
    "603986": "兆易创新",
}

US_STOCKS = {
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "AVGO": "Broadcom",
}


# ============================================================
# 数据获取（用按代码查询的轻量接口，避开慢接口）
# ============================================================

def get_a_stock_one(code, name, retries=3):
    """精准查询单只 A 股 — 用 stock_bid_ask_em，几十 KB 数据"""
    for attempt in range(retries):
        try:
            df = ak.stock_bid_ask_em(symbol=code)
            data = dict(zip(df['item'], df['value']))
            return {
                "code": code,
                "name": name,
                "price": float(data.get('最新', 0)),
                "change_pct": float(data.get('涨幅', 0)),
                "change_amt": float(data.get('涨跌', 0)),
                "open": float(data.get('今开', 0)),
                "high": float(data.get('最高', 0)),
                "low": float(data.get('最低', 0)),
                "prev_close": float(data.get('昨收', 0)),
                "volume": float(data.get('总手', 0)),
                "amount": float(data.get('金额', 0)),
                "turnover_rate": float(data.get('换手', 0)),
                "ok": True,
            }
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.5)  # 等待后重试，避免被风控
                continue
            return {"code": code, "name": name, "ok": False, "error": str(e)}


def get_us_stock_one(ticker, name, retries=3):
    """精准查询单只美股 — 用 stock_us_hist 拿近 5 天日线"""
    for attempt in range(retries):
        try:
            # 美股代码 akshare 格式: 105.NVDA / 106.AVGO
            # 用 stock_us_spot_em 一次拿全表太慢，改用 stock_us_hist
            df = ak.stock_us_daily(symbol=ticker, adjust="")
            if df is None or df.empty:
                raise ValueError("No data")
            df = df.tail(2)
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            change_pct = (last['close'] - prev['close']) / prev['close'] * 100
            return {
                "ticker": ticker,
                "name": name,
                "price": float(last['close']),
                "change_pct": float(change_pct),
                "open": float(last['open']),
                "high": float(last['high']),
                "low": float(last['low']),
                "prev_close": float(prev['close']),
                "volume": float(last['volume']),
                "date": str(last['date']) if 'date' in last.index else str(df.index[-1]),
                "ok": True,
            }
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.5)
                continue
            return {"ticker": ticker, "name": name, "ok": False, "error": str(e)}


def get_indices():
    """获取主要指数"""
    try:
        df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        result = []
        for name in ['上证指数', '深证成指', '创业板指', '科创50', '沪深300']:
            row = df[df['名称'] == name]
            if not row.empty:
                row = row.iloc[0]
                result.append({
                    "name": name,
                    "price": float(row['最新价']),
                    "change_pct": float(row['涨跌幅']),
                })
        return result
    except Exception as e:
        return [{"error": str(e)}]


# ============================================================
# 输出
# ============================================================

def print_report(data):
    """屏幕格式化输出"""
    print("\n" + "█" * 80)
    print(f"  股票监控  -  {data['timestamp']}")
    print("█" * 80)

    print("\n" + "=" * 80)
    print("A 股主要指数")
    print("=" * 80)
    for idx in data['indices']:
        if 'error' in idx:
            print(f"  ❌ {idx['error']}")
            continue
        sym = "↑" if idx['change_pct'] > 0 else ("↓" if idx['change_pct'] < 0 else "—")
        print(f"  {idx['name']:<8s}  {idx['price']:>10.2f}  {sym}  {idx['change_pct']:+.2f}%")

    print("\n" + "=" * 80)
    print("A 股实时行情")
    print("=" * 80)
    for s in data['a_stocks']:
        if not s['ok']:
            print(f"  ❌ {s['code']} {s['name']}: {s.get('error', 'failed')}")
            continue
        sym = "↑" if s['change_pct'] > 0 else ("↓" if s['change_pct'] < 0 else "—")
        amt = f"{s['amount']/1e8:.2f} 亿" if s['amount'] > 1e8 else f"{s['amount']/1e4:.0f} 万"
        print(f"  {s['code']} {s['name']:<8s}  {s['price']:>8.2f}元  {sym} {s['change_pct']:+6.2f}%")
        print(f"      开{s['open']:.2f}  高{s['high']:.2f}  低{s['low']:.2f}  昨收{s['prev_close']:.2f}")
        print(f"      成交额 {amt}  | 换手率 {s['turnover_rate']:.2f}%")

    print("\n" + "=" * 80)
    print("美股行情（近一个交易日收盘）")
    print("=" * 80)
    for s in data['us_stocks']:
        if not s['ok']:
            print(f"  ❌ {s['ticker']} {s['name']}: {s.get('error', 'failed')}")
            continue
        sym = "↑" if s['change_pct'] > 0 else ("↓" if s['change_pct'] < 0 else "—")
        print(f"  {s['ticker']:<6s} {s['name']:<10s}  ${s['price']:>9.2f}  {sym} {s['change_pct']:+6.2f}%  ({s['date']})")
    print()


# ============================================================
# 主程序
# ============================================================

def main():
    export = "--export" in sys.argv

    data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "indices": get_indices(),
        "a_stocks": [get_a_stock_one(c, n) for c, n in A_STOCKS.items()],
        "us_stocks": [get_us_stock_one(t, n) for t, n in US_STOCKS.items()],
    }

    print_report(data)

    if export:
        with open("stocks_data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("\n✅ 已导出 stocks_data.json")
        print("   把这个文件内容复制粘贴给 Claude，或者上传到 Google Drive 让 Claude 读取，")
        print("   Claude 就能基于真实数据帮你做分析、判断止损、对比走势了。\n")


if __name__ == "__main__":
    main()