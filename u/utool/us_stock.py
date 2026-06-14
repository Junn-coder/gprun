#!/usr/bin/env python3
"""
美股价格工具(稳定版)— 行情 + 历史下载,一个文件搞定。

数据源: akshare 的 stock_us_daily (新浪财经)
  关键: 直接用 ticker(NVDA),不调用 stock_us_spot_em。
  旧脚本断连的根源就是 stock_us_spot_em 每次拉 132 页全美股表 ->
  RemoteDisconnected。换成 stock_us_daily 后彻底消失。

设计要点:
  * 一次 stock_us_daily 拿到全历史,再按 --start/--end 切片;实时/最新
    行情就取最后一根日线(美股盘后/休市时本来也只有收盘价)。
  * 增量缓存: price_<TICKER>.txt 已存在时只补缺失日期再合并。
  * 新浪美股只给 volume,没有成交额/换手,Turnover 列留空,Amplitude 自算。

依赖:
    pip install -r requirements.txt   (akshare, pandas)

用法:
    # 最新行情(默认,取最近一个交易日)
    python us_stock.py NVDA AMD AVGO

    # 下载/刷新历史(增量,写到 share_data/price_<TICKER>.txt)
    python us_stock.py NVDA AMD --history
    python us_stock.py NVDA --history --start 2024-01-01 --end 2026-05-27

    # 导出 JSON
    python us_stock.py NVDA AMD --export

    # 下载历史并提交到 GitHub
    python us_stock.py NVDA AMD --history --commit
"""

import sys
import os
import json
import time
import argparse
import subprocess
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

# ------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTDIR = os.path.normpath(os.path.join(HERE, "..", "ushare_data"))
DEFAULT_LOOKBACK_DAYS = 730
COLUMNS = ["Date", "Open", "Close", "High", "Low", "Volume", "Turnover", "Amplitude(%)"]
SOURCE_LABEL = "akshare stock_us_daily (新浪财经)"

NAMES = {
    "NVDA": "NVIDIA", "AMD": "AMD", "AVGO": "Broadcom",
    "TSM": "TSMC", "MU": "Micron", "AAPL": "Apple", "MSFT": "Microsoft",
}


def resolve_name(t):
    return NAMES.get(t.upper(), t.upper())


# ------------------------------------------------------------------
# 全历史抓取(核心,已验证稳定;无 spot 表)
# ------------------------------------------------------------------
def fetch_full(ticker, adjust="qfq"):
    df = ak.stock_us_daily(symbol=ticker.upper(), adjust=adjust)
    if df is None or df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    prev_close = df["close"].shift(1)
    out = pd.DataFrame({
        "Date": df["date"],
        "Open": df["open"], "Close": df["close"],
        "High": df["high"], "Low": df["low"],
        "Volume": df["volume"].astype("int64"),
        "Turnover": "",                                  # 新浪美股无成交额
        "Amplitude(%)": ((df["high"] - df["low"]) / prev_close * 100).round(2),
    })
    return out[COLUMNS]


def with_retry(fn, retries=3, backoff=3.0, label=""):
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            wait = backoff * (i + 1)
            print(f"      ⚠ {label} 第 {i+1}/{retries} 次失败 ({type(e).__name__}),{wait:.0f}s 后重试")
            time.sleep(wait)
    raise last


# ------------------------------------------------------------------
# 行情(取全历史最后一根)
# ------------------------------------------------------------------
def get_quote(ticker):
    name = resolve_name(ticker)
    try:
        df = with_retry(lambda: fetch_full(ticker), label=ticker)
        if df is None or df.empty:
            raise ValueError("no data")
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        chg = float(last["Close"]) - float(prev["Close"])
        return {
            "type": "美股", "ticker": ticker.upper(), "name": name,
            "price": float(last["Close"]), "change_pct": chg / float(prev["Close"]) * 100,
            "change_amt": chg, "open": float(last["Open"]), "high": float(last["High"]),
            "low": float(last["Low"]), "prev_close": float(prev["Close"]),
            "volume": int(last["Volume"]), "date": str(last["Date"]),
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return {"type": "美股", "ticker": ticker.upper(), "name": name, "ok": False, "error": str(e)}


def print_quote(d):
    if not d.get("ok", True):
        print(f"\n❌ {d['ticker']} {d['name']}: {d.get('error')}")
        return
    sym = "↑" if d["change_pct"] > 0 else ("↓" if d["change_pct"] < 0 else "—")
    print(f"\n[美股] {d['ticker']} {d['name']}")
    print(f"  收盘: ${d['price']:.2f}   {sym} {d['change_pct']:+.2f}%  ({d['change_amt']:+.2f})")
    print(f"  开:{d['open']:.2f}  高:{d['high']:.2f}  低:{d['low']:.2f}  昨收:{d['prev_close']:.2f}")
    print(f"  数据日期: {d['date']}   查询: {d['query_time']}")


# ------------------------------------------------------------------
# 增量保存
# ------------------------------------------------------------------
def read_existing(path):
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        hdr = next(i for i, ln in enumerate(lines) if ln.startswith("Date,"))
        from io import StringIO
        df = pd.read_csv(StringIO("".join(lines[hdr:])))
        return df, str(df["Date"].iloc[-1])
    except Exception:
        return None, None


def save_history(ticker, name, outdir, start, end):
    os.makedirs(outdir, exist_ok=True)
    code = ticker.upper()
    path = os.path.join(outdir, f"price_{code}.txt")

    full = with_retry(lambda: fetch_full(ticker), label=code)
    if full is None or full.empty:
        return path, 0, 0
    full = full[(full["Date"] >= start) & (full["Date"] <= end)]

    old, last_date = read_existing(path)
    if old is not None and last_date:
        merged = pd.concat([old.astype({"Date": str}), full]).drop_duplicates(subset="Date", keep="last")
        merged = merged.sort_values("Date").reset_index(drop=True)
        added = len(merged) - len(old)
    else:
        merged, added = full.reset_index(drop=True), len(full)

    header = (
        f"{name} ({code}) - Daily Historical Prices\n"
        f"Source: {SOURCE_LABEL}\n"
        f"Range: {merged['Date'].iloc[0]} to {merged['Date'].iloc[-1]}\n"
        f"Total trading days: {len(merged)}\n\n"
    )
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        merged.to_csv(f, index=False, lineterminator="\n")
    return path, len(merged), added


# ------------------------------------------------------------------
def git_commit(outdir):
    rel = os.path.relpath(outdir, HERE)
    try:
        subprocess.run(["git", "-C", HERE, "add", outdir], check=True)
        msg = f"data: update US prices {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        r = subprocess.run(["git", "-C", HERE, "commit", "-m", msg],
                           capture_output=True, text=True)
        if "nothing to commit" in (r.stdout + r.stderr):
            print("\nℹ️  没有变化,无需提交")
            return
        subprocess.run(["git", "-C", HERE, "push"], check=True)
        print(f"\n✅ 已提交并推送到 GitHub ({rel})")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ git 操作失败: {e}")


# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="美股行情 + 历史下载(稳定版)")
    ap.add_argument("tickers", nargs="+", help="美股代码,如 NVDA AMD")
    ap.add_argument("--history", action="store_true", help="下载/增量刷新历史日线")
    ap.add_argument("--export", action="store_true", help="导出 JSON")
    ap.add_argument("--commit", action="store_true", help="抓取后 git add/commit/push")
    ap.add_argument("--start", default=None, help="起始 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="结束 YYYY-MM-DD,默认今天")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--sleep", type=float, default=2.0, help="每只间隔秒数")
    args = ap.parse_args()

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.strptime(end, "%Y-%m-%d") -
                           timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    results = []
    for i, raw in enumerate(args.tickers):
        t = raw.strip().upper()
        if i > 0:
            time.sleep(args.sleep)
        try:
            if args.history:
                name = resolve_name(t)
                path, total, added = save_history(t, name, args.outdir, start, end)
                print(f"  ✓ {t:<6s} {name:<10s}  共 {total} 行(新增 {added})  →  {path}")
                results.append({"ticker": t, "name": name, "total": total, "added": added})
            else:
                q = get_quote(t)
                print_quote(q)
                results.append(q)
        except Exception as e:
            print(f"  ✗ {t} 失败: {e}")
            results.append({"ticker": t, "ok": False, "error": str(e)})

    if args.export:
        fn = os.path.join(args.outdir, f"us_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        os.makedirs(args.outdir, exist_ok=True)
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已导出 {fn}")

    if args.commit:
        git_commit(args.outdir)


if __name__ == "__main__":
    main()
