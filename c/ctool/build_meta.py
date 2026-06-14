#!/usr/bin/env python3
"""
Build a static metadata map for the backtest: code -> industry + float mcap.

The OHLC files (stock_history_ak/*.csv) carry NO industry and NO market cap, but
the backtest needs both:
  - industry  -> hot-sector grouping (hota.md)
  - float cap -> framed.md §2 cap-OK band (30-500亿 CNY)

Source: THS (同花顺) industry detail pages, scraped directly.
  Why not akshare/Sina/EM:
    - Sina "新浪行业" is a LEGACY classification: covers only ~2962/5175 codes and
      misses ~70% of ChiNext (300/301) and ~98% of STAR (688) — exactly where the
      short-term limit-up action lives. Unusable.
    - EastMoney industry endpoints are blocked on this machine (RemoteDisconnected).
    - akshare 1.18.64 has NO THS constituents function (only board-level info/summary).
  So we scrape THS web pages ourselves. THS is the modern, complete classification
  and its detail table ALSO carries 流通市值, so ONE pass yields industry + float cap.

  Board list : ak.stock_board_industry_name_ths()  -> 90 boards (name, code=881xxx)
  Detail page: http://q.10jqka.com.cn/thshy/detail/code/<bcode>/page/<p>/
               table cols: 序号,代码,名称,现价,...,流通股,流通市值(e.g. "298.90亿"),市盈率
               page_info "x/N" in the HTML gives the page count.

Output: share_data/stock_meta.csv  (code,name,industry,float_mcap_now,total_mcap_now)
  float_mcap_now is in CNY (directly comparable to framed.md's 30e8-500e8 cap-OK band).
  total_mcap_now is left blank (THS detail table has no 总市值 column; framed.md §2
  keys off FLOAT cap, which is what we capture).

Caveat: float mcap here is CURRENT (2026). For precise 2025 cap the backtest can
recompute float_shares * close(date) from Sina stock_zh_a_daily.outstanding_share;
this file is the fast default / cross-check.
"""

import os
import re
import sys
import time
from io import StringIO

import akshare as ak
import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "share_data")
META = os.path.join(OUTDIR, "stock_meta.csv")
OHLC_DIR = os.path.join(HERE, "stock_history_ak")

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "http://q.10jqka.com.cn/thshy/",
}
BASE = "http://q.10jqka.com.cn/thshy/detail/code/{b}/page/{p}/"


def parse_mcap(s):
    """'298.90亿' -> 2.989e10 CNY ; '56.98万' -> 5.698e5 ; '' -> None."""
    if not isinstance(s, str):
        return None
    s = s.strip().replace(",", "")
    m = re.match(r"^([0-9.]+)\s*([亿万]?)", s)
    if not m or not m.group(1):
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit == "亿":
        v *= 1e8
    elif unit == "万":
        v *= 1e4
    return v


def get(url, retries=3, sleep=1.0):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HDR, timeout=15)
            r.encoding = "gbk"
            if r.status_code == 200 and "代码" in r.text:
                return r.text
        except Exception:
            pass
        time.sleep(sleep + 1.5 * attempt)  # gentle backoff; THS throttles bursts
    return None


def fetch_board(bcode, bname):
    """Return list of (code, name, float_mcap_now) for one THS industry board."""
    first = get(BASE.format(b=bcode, p=1))
    if first is None:
        print(f"  x {bname} ({bcode}) page 1 failed")
        return []
    pm = re.search(r"page_info[^0-9]{0,10}(\d+)/(\d+)", first)
    pages = int(pm.group(2)) if pm else 1

    out = []
    for p in range(1, pages + 1):
        html = first if p == 1 else get(BASE.format(b=bcode, p=p))
        if html is None:
            print(f"  ! {bname} page {p}/{pages} failed; skipping page")
            continue
        try:
            df = pd.read_html(StringIO(html))[0]
        except Exception as e:
            print(f"  ! {bname} page {p} parse fail: {type(e).__name__}")
            continue
        if "代码" not in df.columns:
            continue
        for _, r in df.iterrows():
            code = str(r["代码"]).split(".")[0].zfill(6)
            if not code.isdigit():
                continue
            out.append((code, str(r.get("名称", code)), parse_mcap(r.get("流通市值"))))
        if p < pages:
            time.sleep(1.0)  # be polite between pages (THS throttles bursts)
    return out


def ohlc_codes():
    if not os.path.isdir(OHLC_DIR):
        return set()
    return {os.path.splitext(f)[0].zfill(6)
            for f in os.listdir(OHLC_DIR) if f.lower().endswith(".csv")}


def main():
    os.makedirs(OUTDIR, exist_ok=True)

    print("[1] THS industry board list ...")
    boards = ak.stock_board_industry_name_ths()
    pairs = list(zip(boards["name"], boards["code"]))
    print(f"    {len(pairs)} THS industry boards")

    rows = {}  # code -> dict (first board wins; THS = one industry per stock)
    print("[2] scraping THS detail pages (industry + 流通市值) ...")
    for i, (bname, bcode) in enumerate(pairs, 1):
        got = fetch_board(str(bcode), str(bname))
        for code, name, mcap in got:
            if code in rows:
                continue
            rows[code] = {
                "code": code,
                "name": name,
                "industry": bname,
                "float_mcap_now": mcap if mcap is not None else "",
                "total_mcap_now": "",
            }
        print(f"    {i:>2}/{len(pairs)}  {bname:<8}  +{len(got):>4} (total {len(rows)})")
        time.sleep(0.8)

    out = pd.DataFrame(rows.values(),
                       columns=["code", "name", "industry", "float_mcap_now", "total_mcap_now"])
    out = out.sort_values("code").reset_index(drop=True)
    out.to_csv(META, index=False, encoding="utf-8", lineterminator="\n")

    n = len(out)
    cap = (out["float_mcap_now"] != "").sum()
    fv = pd.to_numeric(out["float_mcap_now"], errors="coerce")
    capok = ((out["float_mcap_now"] != "") & fv.between(30e8, 500e8)).sum()

    print("=" * 64)
    print(f" stock_meta.csv written: {META}")
    print(f"   rows (codes)   : {n}")
    print(f"   with industry  : {n} (100%)")
    print(f"   with float cap : {cap}  ({cap/n*100:.1f}%)")
    print(f"   cap-OK 30-500亿: {capok}")

    ohlc = ohlc_codes()
    if ohlc:
        have = set(out["code"])
        miss = sorted(ohlc - have)
        cover = len(ohlc & have)
        print("-" * 64)
        print(f"   OHLC codes     : {len(ohlc)}")
        print(f"   covered        : {cover}  ({cover/len(ohlc)*100:.1f}%)")
        print(f"   MISSING        : {len(miss)}")
        if miss:
            from collections import Counter
            pref = Counter(c[:3] for c in miss).most_common(12)
            print(f"   missing by prefix: {dict(pref)}")
    print("=" * 64)


if __name__ == "__main__":
    main()
