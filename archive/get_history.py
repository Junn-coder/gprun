#!/usr/bin/env python3
"""
历史 OHLCV 价格下载脚本

支持 A 股 / 美股 / A 股指数 / ETF,输出到 share_data/price_<CODE>.txt
格式与 share_data/price_AMD.txt 等已有文件保持一致。

依赖:
    pip install akshare pandas

用法:
    # 单只(默认 2 年日线)
    python get_history.py 601991
    python get_history.py NVDA

    # 批量
    python get_history.py 601991 600726 600406 NVDA AMD

    # 指数
    python get_history.py --index 000300 399006 000001

    # ETF (同 A 股代码格式)
    python get_history.py 512480 562500 159611

    # 自定义起止日期 + 周线
    python get_history.py 601991 --start 2024-01-01 --end 2026-05-26 --period weekly

    # 输出到自定义目录
    python get_history.py 601991 --outdir /tmp/data
"""

import sys
import os
import time
import argparse
from datetime import datetime, timedelta
import akshare as ak

_A_SPOT_CACHE = None


DEFAULT_OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "share_data")
DEFAULT_LOOKBACK_DAYS = 730  # ~ 2 years


def is_a_stock_code(code):
    """6 位数字 = A 股 / ETF / 申万行业代码均可走 stock_zh_a_hist。"""
    return code.isdigit() and len(code) == 6


def is_index_code(code):
    """A 股指数同样是 6 位数字,但走不同接口。需显式 --index 标记。"""
    return code.isdigit() and len(code) == 6


def fmt_date(d):
    """akshare A 股接口接受 YYYYMMDD 字符串。"""
    return d.replace("-", "")


def download_a_stock(code, start, end, period, adjust="qfq"):
    """A 股 / ETF 日线 / 周线。adjust=qfq 用前复权,适合做技术分析。"""
    df = ak.stock_zh_a_hist(
        symbol=code,
        period=period,
        start_date=fmt_date(start),
        end_date=fmt_date(end),
        adjust=adjust,
    )
    if df is None or df.empty:
        return None
    df = df.rename(columns={
        "日期": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
        "成交额": "Turnover",
        "振幅": "Amplitude(%)",
    })
    return df[["Date", "Open", "Close", "High", "Low", "Volume", "Turnover", "Amplitude(%)"]]


def download_a_index(code, start, end, period):
    """A 股指数(000300 / 399006 / 000001 等)。"""
    df = ak.index_zh_a_hist(
        symbol=code,
        period=period,
        start_date=fmt_date(start),
        end_date=fmt_date(end),
    )
    if df is None or df.empty:
        return None
    df = df.rename(columns={
        "日期": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
        "成交额": "Turnover",
        "振幅": "Amplitude(%)",
    })
    keep = [c for c in ["Date", "Open", "Close", "High", "Low", "Volume", "Turnover", "Amplitude(%)"] if c in df.columns]
    return df[keep]


def download_us_stock(ticker, start, end):
    """美股日线,前复权。akshare 美股接口走 stock_us_hist。"""
    # akshare 美股代码可能需要交易所前缀,先 lookup
    spot = ak.stock_us_spot_em()
    spot["ticker"] = spot["代码"].str.split(".").str[-1]
    match = spot[spot["ticker"] == ticker.upper()]
    if match.empty:
        return None
    symbol = match.iloc[0]["代码"]  # 形如 "105.NVDA"

    df = ak.stock_us_hist(
        symbol=symbol,
        period="daily",
        start_date=fmt_date(start),
        end_date=fmt_date(end),
        adjust="qfq",
    )
    if df is None or df.empty:
        return None
    df = df.rename(columns={
        "日期": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
        "成交额": "Turnover",
        "振幅": "Amplitude(%)",
    })
    keep = [c for c in ["Date", "Open", "Close", "High", "Low", "Volume", "Turnover", "Amplitude(%)"] if c in df.columns]
    return df[keep]


def save_to_file(df, code, name, outdir, source_label):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"price_{code}.txt")
    start = df["Date"].iloc[0]
    end = df["Date"].iloc[-1]
    header = (
        f"{name} ({code}) - Daily Historical Prices\n"
        f"Source: {source_label}\n"
        f"Range: {start} to {end}\n"
        f"Total trading days: {len(df)}\n"
        f"\n"
    )
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        df.to_csv(f, index=False, lineterminator="\n")
    return path


def resolve_a_name(code):
    """通过 spot 接口拿中文名 — 全市场 spot 只拉一次,缓存供后续复用。"""
    global _A_SPOT_CACHE
    try:
        if _A_SPOT_CACHE is None:
            _A_SPOT_CACHE = ak.stock_zh_a_spot_em()
        match = _A_SPOT_CACHE[_A_SPOT_CACHE["代码"] == code]
        if not match.empty:
            return match.iloc[0]["名称"]
    except Exception:
        pass
    return code


def with_retry(fn, retries=3, backoff=3.0, label=""):
    """对易断连的网络调用做指数退避重试。"""
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            wait = backoff * (attempt + 1)
            print(f"      ⚠ {label} 第 {attempt+1}/{retries} 次失败 ({type(e).__name__}),{wait:.0f}s 后重试")
            time.sleep(wait)
    raise last_err


def main():
    ap = argparse.ArgumentParser(description="Download historical OHLCV for A-share / US / index / ETF.")
    ap.add_argument("codes", nargs="+", help="股票代码(A 股 6 位 / 美股 ticker)")
    ap.add_argument("--index", action="store_true", help="按 A 股指数处理(000300 / 399006 / 000001 等)")
    ap.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"], help="K 线周期")
    ap.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD,默认今天")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR, help=f"输出目录,默认 {DEFAULT_OUTDIR}")
    ap.add_argument("--sleep", type=float, default=2.0, help="每只之间间隔(秒),避免被东财风控,默认 2")
    ap.add_argument("--retries", type=int, default=3, help="单只下载重试次数,默认 3")
    args = ap.parse_args()

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    print(f"Range: {start} → {end}  |  Period: {args.period}  |  Out: {args.outdir}")
    print("=" * 72)

    for i, raw in enumerate(args.codes):
        code = raw.strip()
        if i > 0:
            time.sleep(args.sleep)
        try:
            if args.index and is_index_code(code):
                df = with_retry(lambda: download_a_index(code, start, end, args.period),
                                retries=args.retries, label=code)
                src = "akshare index_zh_a_hist (东方财富)"
                name = code
            elif is_a_stock_code(code):
                df = with_retry(lambda: download_a_stock(code, start, end, args.period),
                                retries=args.retries, label=code)
                src = "akshare stock_zh_a_hist (东方财富, 前复权)"
                name = resolve_a_name(code)
            else:
                df = with_retry(lambda: download_us_stock(code, start, end),
                                retries=args.retries, label=code)
                src = "akshare stock_us_hist (东方财富, 前复权)"
                name = code.upper()

            if df is None or df.empty:
                print(f"  ✗ {code:<8s} 无数据返回")
                continue

            path = save_to_file(df, code, name, args.outdir, src)
            print(f"  ✓ {code:<8s} {name:<14s}  {len(df):>4d} 行  →  {path}")
        except Exception as e:
            print(f"  ✗ {code:<8s} 失败: {e}")


if __name__ == "__main__":
    main()
