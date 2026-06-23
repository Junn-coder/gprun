# TODO — A股短线回测 (resume notes)

_Last updated: 2026-05-31_

## ✅ DONE — metadata (step 2)
- `tool/build_meta.py` rewritten to scrape **THS 同花顺行业详情页** directly
  (akshare 1.18.64 has no THS cons fn; EM blocked; Sina industry too incomplete).
- Output: `tool/share_data/stock_meta.csv` — cols `code,name,industry,float_mcap_now,total_mcap_now`.
  - 4665 rows, 100% have industry + float_mcap (CNY). cap-OK(50–500亿)=2301.
  - Covers **4356 / 5175** OHLC codes (84.2%). Missing 819 = long tail of big boards
    (THS unauth caps at 5 pages = 100 stocks/board). Acceptable — leaders all covered.

## ▶ NEXT — strategy backtest (`tool/strategy_backtest.py`)
Goal: replay framed.md on 2025 and compute P&L.

Rules (agreed):
1. Trade only on **GREEN** gate days (2025 had 78 — from `gate_backtest.py`).
2. Each GREEN day: scanner picks **top-3** candidates; **buy 2** that are **cap-OK** (50–500亿).
   - If fewer than 2 cap-OK → take what's there; if **none** cap-OK → fall back to **#1**.
   - **Skip 一字涨停** (open==high==close==limit; can't actually fill).
3. Position size: **¥25,000 each**.
4. Exits: framed.md **§4** (max ~10 trading days, +8%/+15% scale-outs, 5MA trail after +10%,
   stop −5~8% / below structure). Mechanical, next-open execution.
5. Output: per-trade log + total 2025 P&L.

Data plumbing:
- Limit-up pool for each 2025 day must be **reconstructed from OHLC** (`stock_history_ak/*.csv`),
  since EM `stock_zt_pool_em` only serves recent dates (2025 = 0 rows).
  Limit detection: close/prev_close ≈ +10% main board, +20% ChiNext(300/301)/STAR(688), +30% BSE(920).
- Industry/cap for candidates: read `share_data/stock_meta.csv`.
- For the 819 missing codes (no cap in meta): lazy-load **Sina** `stock_zh_a_daily.outstanding_share × close(date)`
  → 2025-accurate float mcap.

Validation order:
- **First** run only **2025-01** (4 GREEN days: 2025-01-17, 01-20, 01-21, 01-24) → eyeball trades.
- Then run full 2025.

## Key gotchas (don't re-learn)
- EastMoney endpoints blocked on this machine (RemoteDisconnected). Index/quotes = **Sina only**.
  scan_cn.py keeps EM only for the live limit-up pool (EM-exclusive); not used in backtest.
- Windows cp1252 console: every script needs the `sys.stdout.reconfigure(encoding="utf-8")` shim.
- akshare pinned 1.18.63/64 for cross-platform consistency — don't upgrade.
- read_html drops leading zeros on codes → always `.zfill(6)`.

## Reference files
- Strategy: `c/main/framed.md` (§1 gate, §2 cap, §3 entries, §4 exits, §5 sizing).
- Hot sectors: `c/main/hota.md`. Workflow: `c/main/steps.md`, prompt: `c/main/cprompt.md`.
- Gate backtest (done): `tool/gate_backtest.py` → 2025: 78 GREEN / 69 AMBER / 96 RED.
