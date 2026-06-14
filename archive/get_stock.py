#!/usr/bin/env python3
"""
A 股 + 美股价格监控脚本
适用平台: Windows / Linux / Mac
依赖: pip install akshare pandas

使用前安装依赖:
    pip install akshare pandas

直接运行:
    python stock_monitor.py
"""

import akshare as ak
import pandas as pd
from datetime import datetime


# ============================================================
# 配置区：在这里修改你想跟踪的股票
# ============================================================

# A 股代码（6 位数字，不需要后缀）
A_STOCKS = {
    "300308": "中际旭创",
    "688008": "澜起科技",
    "603986": "兆易创新",
    # 在下方继续添加你想跟踪的 A 股
    # "000001": "平安银行",
}

# 美股代码（标准美股 ticker）
US_STOCKS = {
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    # 在下方继续添加你想跟踪的美股
    # "AAPL": "Apple",
    # "MSFT": "Microsoft",
}


# ============================================================
# 价格止损位（可选）
# ============================================================
# 格式: 股票代码 -> (买入价, 止损跌幅%)
# 跑脚本时会自动计算止损价并提示
STOP_LOSS = {
    # "300308": (880, 12),  # 买入价 880 元，跌 12% 止损
    # "688008": (250, 10),  # 买入价 250 元，跌 10% 止损
}


# ============================================================
# 主程序
# ============================================================

def get_a_stock_prices():
    """获取 A 股实时报价"""
    print("\n" + "=" * 80)
    print(f"A 股实时行情  (查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print("=" * 80)

    try:
        df = ak.stock_zh_a_spot_em()
        result = df[df['代码'].isin(A_STOCKS.keys())].copy()

        if result.empty:
            print("未查询到指定股票数据")
            return

        # 按我们定义的顺序排序
        result['排序'] = result['代码'].map({c: i for i, c in enumerate(A_STOCKS.keys())})
        result = result.sort_values('排序')

        for _, row in result.iterrows():
            code = row['代码']
            name = A_STOCKS.get(code, row['名称'])
            price = row['最新价']
            change_pct = row['涨跌幅']
            change_amt = row['涨跌额']
            volume = row['成交额']  # 单位元

            # 涨跌符号
            symbol = "↑" if change_pct > 0 else ("↓" if change_pct < 0 else "—")

            line = f"  {code} {name:<8s}  {price:>8.2f}元  {symbol} {change_pct:+6.2f}%  ({change_amt:+.2f})"

            # 检查止损
            if code in STOP_LOSS:
                buy_price, stop_pct = STOP_LOSS[code]
                stop_price = buy_price * (1 - stop_pct / 100)
                current_loss = (price - buy_price) / buy_price * 100
                if price <= stop_price:
                    line += f"  ⚠️ 触发止损！(买入 {buy_price}, 当前 {current_loss:+.2f}%)"
                else:
                    line += f"  | 持仓盈亏 {current_loss:+.2f}% (止损位 {stop_price:.2f})"

            print(line)

            # 成交额转换为亿元
            if volume > 1e8:
                vol_str = f"{volume / 1e8:.2f} 亿"
            elif volume > 1e4:
                vol_str = f"{volume / 1e4:.0f} 万"
            else:
                vol_str = f"{volume:.0f}"
            print(f"      成交额: {vol_str}  | 换手率: {row['换手率']:.2f}%  | 市值: {row['总市值']/1e8:.0f} 亿")

    except Exception as e:
        print(f"  ❌ A 股查询失败: {e}")


def get_us_stock_prices():
    """获取美股实时报价"""
    print("\n" + "=" * 80)
    print(f"美股实时行情  (注意美股开盘时间，北京时间 21:30 - 次日 04:00)")
    print("=" * 80)

    try:
        df = ak.stock_us_spot_em()
        # 美股代码格式在 akshare 里是 "105.NVDA" 这种，需要提取 ticker 部分
        df['ticker'] = df['代码'].str.split('.').str[-1]
        result = df[df['ticker'].isin(US_STOCKS.keys())].copy()

        if result.empty:
            print("未查询到指定美股数据")
            return

        for _, row in result.iterrows():
            ticker = row['ticker']
            name = US_STOCKS.get(ticker, row['名称'])
            price = row['最新价']
            change_pct = row['涨跌幅']

            symbol = "↑" if change_pct > 0 else ("↓" if change_pct < 0 else "—")

            print(f"  {ticker:<6s} {name:<15s}  ${price:>10.2f}  {symbol} {change_pct:+6.2f}%")

    except Exception as e:
        print(f"  ❌ 美股查询失败: {e}")


def get_indices():
    """获取 A 股主要指数"""
    print("\n" + "=" * 80)
    print("A 股主要指数")
    print("=" * 80)

    try:
        df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        target_indices = ['上证指数', '深证成指', '创业板指', '科创50', '沪深300']

        for name in target_indices:
            row = df[df['名称'] == name]
            if not row.empty:
                row = row.iloc[0]
                price = row['最新价']
                change_pct = row['涨跌幅']
                symbol = "↑" if change_pct > 0 else ("↓" if change_pct < 0 else "—")
                print(f"  {name:<8s}  {price:>10.2f}  {symbol} {change_pct:+6.2f}%")
    except Exception as e:
        print(f"  ❌ 指数查询失败: {e}")


if __name__ == "__main__":
    print("\n" + "█" * 80)
    print(f"  Jun 的股票监控  -  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("█" * 80)

    get_indices()
    get_a_stock_prices()
    get_us_stock_prices()

    print("\n" + "─" * 80)
    print("提示：")
    print("  - A 股交易时间: 9:30-11:30, 13:00-15:00 (周一至周五)")
    print("  - 美股交易时间: 北京时间 21:30 - 次日 04:00（夏令时）")
    print("  - 非交易时段显示的是上一个交易日的收盘价")
    print("─" * 80 + "\n")