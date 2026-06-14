#!/usr/bin/env python3
"""
A 股价格工具(稳定版)— 行情 + 历史下载,一个文件搞定。

数据源: akshare(新浪)
  - 历史日线: stock_zh_a_daily (新浪, 前复权)   <- 东方财富常被限流断连,已移除
  - 实时行情: stock_zh_a_daily (新浪, EOD)

设计要点(为什么这样写):
  * 绝不在普通抓取里拉全市场 spot 表 —— 那正是旧脚本断连的根源。
  * 增量缓存: price_<CODE>.txt 已存在时只补最近缺失的几天再合并,
    单次请求很小,失败也不会丢历史。
  * 名称解析不阻塞抓取: 内置 NAMES 字典 + 可选缓存,查不到就用代码。

依赖:
    pip install -r requirements.txt   (akshare, pandas)

用法:
    # 实时/最新行情(默认)
    python cn_stock.py 601991 600726 300308

    # 下载/刷新历史(增量,写到 share_data/price_<CODE>.txt)
    python cn_stock.py 601991 600726 --history
    python cn_stock.py 601991 --history --start 2024-01-01 --end 2026-05-27

    # 导出 JSON(贴给 Claude 分析)
    python cn_stock.py 601991 300308 --export

    # 下载历史并提交到 GitHub(手机一条命令更新数据)
    python cn_stock.py 601991 600726 --history --commit
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

# 强制 UTF-8 输出,避免 ↪ ✓ ✗ ⚠ 等字符在 Windows cp1252 控制台崩溃;
# Linux 默认 UTF-8,此处为 no-op。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTDIR = os.path.join(HERE, "share_data")
DEFAULT_LOOKBACK_DAYS = 730          # 新建文件时默认抓 ~2 年
COLUMNS = ["Date", "Open", "Close", "High", "Low", "Volume", "Turnover", "Amplitude(%)"]
SOURCE_LABEL = "akshare stock_zh_a_daily (新浪, 前复权)"

# 内置中文名(可自行增删,纯粹为了打印好看,查不到不影响抓取)
NAMES = {
    "601991": "大唐发电",
    "600726": "华电能源",
    "600406": "国电南瑞",
    "300308": "中际旭创",
    "688008": "澜起科技",
    "603986": "兆易创新",
    "688256": "寒武纪",
    "300782": "卓胜微",
    "603501": "韦尔股份",
}


def is_a_code(code):
    return code.isdigit() and len(code) == 6


def fmt(d):
    return d.replace("-", "")


def resolve_name(code):
    """先内置字典,再代码本身 —— 永不阻塞抓取。"""
    return NAMES.get(code, code)


# ------------------------------------------------------------------
# 历史抓取(核心,已验证稳定)
# ------------------------------------------------------------------
def fetch_history(code, start, end, adjust="qfq"):
    """新浪日线(stock_zh_a_daily)为唯一源。东方财富 stock_zh_a_hist 常被 IP
    限流断连,已移除。"""
    return _hist_sina(code, start, end, adjust)


def _hist_sina(code, start, end, adjust):
    """新浪日线(不同服务器,东财限流时仍可用)。volume 单位是股,÷100 对齐东财的手。"""
    sina = f"sh{code}" if code.startswith("6") else f"sz{code}"
    df = ak.stock_zh_a_daily(symbol=sina, adjust=adjust)
    if df is None or df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    prev_close = df["close"].shift(1)
    out = pd.DataFrame({
        "Date": df["date"], "Open": df["open"], "Close": df["close"],
        "High": df["high"], "Low": df["low"],
        "Volume": (df["volume"] / 100).round().astype("int64"),   # 股 -> 手,对齐东财
        "Turnover": df["amount"].round(0) if "amount" in df.columns else "",
        "Amplitude(%)": ((df["high"] - df["low"]) / prev_close * 100).round(2),
    })
    out = out[(out["Date"] >= start) & (out["Date"] <= end)]
    return out[COLUMNS].reset_index(drop=True)


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
# 实时行情(新浪 EOD)
# ------------------------------------------------------------------
def quote_via_sina(code):
    sina = f"sh{code}" if code.startswith("6") else f"sz{code}"
    df = ak.stock_zh_a_daily(symbol=sina, adjust="")
    if df is None or df.empty:
        raise ValueError("Sina empty")
    df = df.tail(2)
    last, prev = df.iloc[-1], df.iloc[-2] if len(df) > 1 else df.iloc[-1]
    amt = last["close"] - prev["close"]
    return {
        "price": float(last["close"]), "change_pct": float(amt / prev["close"] * 100),
        "change_amt": float(amt), "open": float(last["open"]), "high": float(last["high"]),
        "low": float(last["low"]), "prev_close": float(prev["close"]),
        "amount": float(last.get("amount", 0) or 0), "turnover_rate": 0.0,
        "source": "新浪财经(历史)",
    }


def get_quote(code):
    name = resolve_name(code)
    for src, fn in [("新浪", quote_via_sina)]:
        for attempt in range(2):
            try:
                data = fn(code)
                data.update({"type": "A股", "code": code, "name": name,
                             "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                return data
            except Exception as e:
                last_err = f"{src}: {e}"
                if attempt == 0:
                    time.sleep(2)
        time.sleep(1)
    return {"type": "A股", "code": code, "name": name, "ok": False, "error": last_err}


def print_quote(d):
    if not d.get("ok", True):
        print(f"\n❌ {d['code']} {d['name']}: {d.get('error')}")
        return
    sym = "↑" if d["change_pct"] > 0 else ("↓" if d["change_pct"] < 0 else "—")
    amt = d.get("amount", 0)
    amt_s = f"{amt/1e8:.2f}亿" if amt >= 1e8 else (f"{amt/1e4:.0f}万" if amt > 0 else "—")
    print(f"\n[A股] {d['code']} {d['name']}  (源: {d['source']})")
    print(f"  最新: {d['price']:.2f} 元   {sym} {d['change_pct']:+.2f}%  ({d['change_amt']:+.2f})")
    print(f"  开:{d['open']:.2f}  高:{d['high']:.2f}  低:{d['low']:.2f}  昨收:{d['prev_close']:.2f}")
    if amt > 0:
        print(f"  成交额: {amt_s}   换手率: {d.get('turnover_rate', 0):.2f}%")
    print(f"  时间: {d['query_time']}")


# ------------------------------------------------------------------
# 增量保存
# ------------------------------------------------------------------
def read_existing(path):
    """返回已存在文件的 DataFrame(只含数据行)和最后日期。没有则 (None, None)。"""
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


def save_history(code, name, outdir, start, end):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"price_{code}.txt")

    old, last_date = read_existing(path)
    if old is not None and last_date:
        # 增量: 从最后日期前推 5 天(覆盖可能的复权回填)抓到 end
        fetch_start = (datetime.strptime(last_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
        new = with_retry(lambda: fetch_history(code, fetch_start, end), label=code)
        if new is None or new.empty:
            return path, len(old), 0
        merged = pd.concat([old, new]).drop_duplicates(subset="Date", keep="last")
        merged = merged.sort_values("Date").reset_index(drop=True)
        added = len(merged) - len(old)
    else:
        merged = with_retry(lambda: fetch_history(code, start, end), label=code)
        if merged is None or merged.empty:
            return path, 0, 0
        added = len(merged)

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
# git 提交(手机更新 GitHub 用)
# ------------------------------------------------------------------
def git_commit(outdir):
    rel = os.path.relpath(outdir, HERE)
    try:
        subprocess.run(["git", "-C", HERE, "add", outdir], check=True)
        msg = f"data: update CN prices {datetime.now().strftime('%Y-%m-%d %H:%M')}"
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
    ap = argparse.ArgumentParser(description="A 股行情 + 历史下载(稳定版)")
    ap.add_argument("codes", nargs="+", help="A 股代码(6 位)")
    ap.add_argument("--history", action="store_true", help="下载/增量刷新历史日线")
    ap.add_argument("--export", action="store_true", help="导出 JSON")
    ap.add_argument("--commit", action="store_true", help="抓取后 git add/commit/push")
    ap.add_argument("--start", default=None, help="起始 YYYY-MM-DD(仅新建文件时)")
    ap.add_argument("--end", default=None, help="结束 YYYY-MM-DD,默认今天")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--sleep", type=float, default=2.0, help="每只间隔秒数")
    args = ap.parse_args()

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.strptime(end, "%Y-%m-%d") -
                           timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    results = []
    for i, raw in enumerate(args.codes):
        code = raw.strip()
        if not is_a_code(code):
            print(f"  ✗ {code} 不是 6 位 A 股代码,跳过(美股请用 us_stock.py)")
            continue
        if i > 0:
            time.sleep(args.sleep)
        try:
            if args.history:
                name = resolve_name(code)
                path, total, added = save_history(code, name, args.outdir, start, end)
                print(f"  ✓ {code} {name:<8s}  共 {total} 行(新增 {added})  →  {path}")
                results.append({"code": code, "name": name, "total": total, "added": added})
            else:
                q = get_quote(code)
                print_quote(q)
                results.append(q)
        except Exception as e:
            print(f"  ✗ {code} 失败: {e}")
            results.append({"code": code, "ok": False, "error": str(e)})

    if args.export:
        fn = os.path.join(args.outdir, f"cn_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        os.makedirs(args.outdir, exist_ok=True)
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已导出 {fn}")

    if args.commit:
        git_commit(args.outdir)


if __name__ == "__main__":
    main()
