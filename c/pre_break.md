# pre_break.py — Pre-breakout scanner (companion to framed.md §3B)

Scans A-share stocks for pre-breakout setups BEFORE the limit-up fires. Complements
`scan_cn.py` which only sees stocks after they've already sealed at +10%.

Precision-first: 4 candidates per day, each with a breakout probability (0–100).
Fake positives penalised harder than missed opportunities.

---

## Input files (all local — zero network calls)

| File | Provides |
|------|----------|
| `tool/stock_history_ak/<code>.csv` (948 files, the `hot_a_stocks.csv` universe) | Daily OHLCV per stock — compute 20-day highs, MAs, volume ratios |
| `tool/share_data/stock_meta.csv` (4666 rows) | `float_mcap_now` for 5B–50B filter, `industry` for sector clustering |
| `tool/all_a_stocks.csv` | symbol→name fallback |

---

## Filters (framed.md aligned)

| Filter | Threshold |
|--------|-----------|
| Free-float market cap | ¥5B – ¥50B |
| Proximity to 20-day high | -5% to +3% (consolidating near high, not yet runaway) |
| Volume expansion | ≥ 1.2× 5-day average volume |
| Not limit-up today | close < prev_close × 1.099 (main) / 1.199 (ChiNext/STAR) |
| Not ST / *ST | excluded by name |

---

## Results

The 6test.md has two parts: 1) names; 2) results;
Output each stock's price change in total 10 market days into file 6test.md part2 in a overwrite method. Don't overwrite the Names part;
pre_break.py should update the part 1 of 6test.md which is Names part.

.venv/bin/python tool/pre_break.py --date 2025-09-15   # -> Part 1
.venv/bin/python tool/grade_6test.py                   # -> Part 2

+---------+------------------------+------------------------------------------+
| stage   | where                  | what it does                             |
+---------+------------------------+------------------------------------------+
| GATES   | scan_day() pre-filter  | hard pass/fail. Throws out most stocks   |
|         | (pre_break.py:210-269) | BEFORE any score exists:                 |
|         |                        |  - cap 5B-50B, not ST   (load_meta)      |
|         |                        |  - >=120 days of history                 |
|         |                        |  - proximity -5% to +3% of 20d high      |
|         |                        |  - volume >= 1.2x 5d avg                 |
|         |                        |  - NOT already limit-up today            |
+---------+------------------------+------------------------------------------+
| RANKER  | compute_score()        | only sees the survivors. Gives each a    |
|         | (pre_break.py:109-172) | 0-100 score, drops <45, sorts, takes top |
|         |                        | TOP_N (now 4).                           |
+---------+------------------------+------------------------------------------+

Want to change WHICH stocks even qualify?   -> tune the GATES (scan_day filters)
Want to change the ORDER / which 4 win?     -> tune compute_score weights

---

## Prerequisites & data freshness

Each stock needs **at least 120 trading days** (~6 calendar months) of price history
in `stock_history_ak/<code>.csv` before it can be scored. Stocks with fewer rows
(e.g. IPOs listed < 6 months ago) are silently skipped.

| Indicator | Lookback |
|-----------|----------|
| 20-day high | 20 days |
| 5d / 10d / 20d MAs | 20 days |
| 5-day avg volume | 5 days |
| Recent limit-up check | 20 days |
| Safety margin (avoids edge cases) | 120 days total |

**Before running a live forecast**, update your price data to include recent trading
days. The existing `cn_stock.py --history` fetches only missing days and appends:

```bash
# Update a single stock
python tool/cn_stock.py <code> --history

# Or batch-update all files (if downa.py is set up for this)
python tool/downa.py
```

---

## Usage

```bash
# Activate venv first
source .venv/bin/activate

# Scan latest trading day
python tool/pre_break.py

# Scan a specific day
python tool/pre_break.py --date 2025-01-15

# Scan a range (for backtesting)
python tool/pre_break.py --range 202501 202506

# Quiet: save files only, no stdout
python tool/pre_break.py -q
```

Output saved to `tool/share_data/prebreak_<date>.txt`.

---

## How it fits the workflow

```
Existing:  index.py (gate) → scan_cn.py (limit-up pool) → cn_stock.py --history (validate)
New:       pre_break.py (pre-breakout pool) → cn_stock.py --history (validate)
```

Run `pre_break.py` alongside `scan_cn.py`. The limit-up scanner tells you what's hot;
the pre-breakout scanner tells you what's ABOUT to be hot and still buyable at the open the next day.

Next step after getting candidates: run `cn_stock.py <code> --history` to validate framed.md §3 entry triggers on each candidate before placing an order.

---

## Backtesting — the scoreboard (`backtest.py`)

Runs `pre_break`'s selection over EVERY trading day in a range, grades each pick by its
forward return, and prints ONE summary you can compare across parameter changes. Reuses
the live `scan_day` (same gates + scoring) and `grade_6test.grade_one` (same forward
return), so the numbers match what runs live. All data is local.

```bash
source .venv/bin/activate

# Baseline over a range (YYYYMM or YYYYMMDD)
python tool/backtest.py --range 202501 202506

# Tune the win rule / hold window / picks-per-day
python tool/backtest.py --range 20250101 20250630 --win 7 --hold 10 --top 4

# Also dump every pick for deep inspection
python tool/backtest.py --range 202501 202506 --csv picks.csv
```

Tuning loop: run baseline -> change ONE knob in `pre_break.py` -> re-run -> compare
hit-rate / avg-return. A good number on ONE range proves nothing — validate on a
SECOND untouched range before believing any change.

Output: only `tool/share_data/backtest_<start>_<end>.txt` (plus the `--csv` file if
that flag is given). Note: re-scans ~948 stocks per trading day with no caching, so a
6-month range takes a few minutes.

====

+---+--------------------------------------------------+
| 1 | Decide what "a win" means (e.g. +7% in 10 days). |
| 2 | Run the harness -> get today's hit rate, say 55%.|
| 3 | Change ONE number (one weight, one threshold).   |
| 4 | Run the harness again -> 55% become better/worse?|
| 5 | Keep the change if better, throw it away if not. |
| 6 | Repeat.                                          |
+---+--------------------------------------------------+

---

## Findings & rebuild — 2026-06-02

NOTE: the "Filters" and "Scoring" sections near the top describe the ORIGINAL
framed.md strategy. They are now SUPERSEDED by the rebuild below. Old version
preserved at `tool/pre_break_proximity_backup.py`.

### How we got here (hunting, not filtering)

Built three local-only tools and let the data, not intuition, choose the gates:

- `grade_6test.py`  — grades one day's picks by 10-day forward return (next-day
  open -> day-10 close), writes Part 2 of `c/6test.md`.
- `backtest.py`     — the scoreboard: runs the live `scan_day` over every trading
  day in a range, grades each pick, prints hit-rate / avg-return / best-worst /
  score buckets. One output file: `share_data/backtest_<start>_<end>.txt`.
- `winner_study.py` — reverse-engineers what PRECEDES a +30%/10d move: snapshots
  precursor features for every stock-day and compares winners vs the whole
  population (lift). Output: `share_data/winner_study.txt`.

### What the data said

1. The original core gate — "consolidate within -5%..+3% of the 20-DAY high" — is DEAD. 0.9-1.1x lift in every period (2024/2025/2026). No edge.

2. What actually precedes a +30% move is MOMENTUM near LONG-TERM highs:

   ```
   feature                  best-bin lift   validated 2024 / 2025 / 2026
   20-day momentum >= ~13%      2.3x            1.6x / 2.8x / 2.6x
   5-day  momentum >= ~5%       1.9x            1.3x / 2.2x / 2.2x
   within ~9% of 250d high      1.9x            n/a  / 2.0x / 2.0x
   vol / prior-20d avg >= 1.4   1.4x            1.4x / 1.6x / 1.2x
   MA 5>10>20 + above 20d MA    1.3-1.4x        consistent
   ```

3. Rebuilt `scan_day` gates around the above; dropped the 5B-50B cap band (weak
   predictor) in favour of a turnover floor (liquidity/buyability). Rank by mom20.

### Backtest, H1 2025 (same +7%/10d win bar), old vs new

```
                 OLD (20d-high)   NEW (momentum)
hit rate             21.5%            25.8%      better
avg return          +0.80%           +1.35%      better
median return       -0.49%           -0.64%      still negative
```

Better, but modest — the gain is a fatter right tail (lottery), not a reliably-up
pick; the median pick still loses over 10 days.

### Open issue (next lever, NOT yet done)

Ranking by HIGHEST mom20 is backwards. In the H1 backtest the most over-extended
bucket (mom20 >= 80) had the WORST avg return (-2.6%), while moderate momentum
(mom20 45-59) was best (+2.2%). Extreme momentum = both tails fatten and reversion
wins on average. This does NOT contradict winner_study (which measured probability
of a big move, not average return). Next experiment: cap mom20 / rank toward the
moderate sweet spot, then validate on the UNTOUCHED H2 2025 before believing it.

### Caveats

- All local data (2024-2026) is broadly bullish/volatile; momentum is exactly the
  style that inverts in a sustained downtrend. No bear slice tested -> a market-
  regime gate (only hunt when the index is healthy) is the obvious missing guard.
- +30%/10d is rare (base rate ~2%); best precursors only lift it to ~4-5%. The
  realistic goal is tilting odds, not making +30% the norm.
- For any AVG-return tuning, winsorize forward returns (a few +400% names like
  688585 distort the mean); the binary hit-rate is robust to them.

---

## Pure-up turn study — 2026-06-03

A second, cleaner hunt: instead of tuning gates by hand, LABEL real episodes and try to
TRAIN a recogniser. Goal (J): a 0-100 scorer that gives HIGH (>60) to pre-breakout setups
and LOW (<30) to noise.

### Sample definition (30-day windows)

period = 30 days = 20 "before" + 10 forward. Decision day D = end of the before-window.
The picker may ONLY see the first 20 days; the last 10 are the outcome (NO look-ahead).

- POSITIVE ("break", loose pure-up): entry = open[D+1], exit = close[D+10],
  net >= +10% AND no close more than 5% below entry (a clean ride, not a spike-and-crash).
- NEGATIVE ("flat", no profit chance): forward 10-day net within +/-5% (sideways).

### Data prepared (one file per episode, 30 rows full OHLCV, zero-padded symbol)

+------------------+--------+----------------------+------------------------------+
| folder           | count  | years (decision day) | what                         |
+------------------+--------+----------------------+------------------------------+
| tool/break_data  | 10,464 | 2025 + 2026          | matched pure-up turns (pos)  |
| tool/flat_data   |  5,000 | 2024 + 2025 + 2026   | flat / no-profit (neg)       |
+------------------+--------+----------------------+------------------------------+

break_data was 18,092 incl 2024; the 2024 episodes were removed. flat_data sampled (fixed
seed 42) from 16,452 distinct non-overlapping flats; J chose to leave the 2024 flats in.

### Tools built (all local; one-off `pip install scikit-learn` was the only network use)

- build_turn_dataset.py    -> flattened pos + all-else dataset (share_data/turn_dataset.parquet)
- extract_break_samples.py -> writes matched 30-day positives into break_data/
- extract_flat_samples.py  -> writes flat 30-day negatives into flat_data/
- train_break.py           -> trains the 0-100 scorer (HistGB + logistic), 20% held-out test.
  Features = FIRST 20 DAYS ONLY: r0..r19 (daily return %), vr0..vr19 (volume / 20d-avg),
  mcap_b — recomputed from full history at D so the oldest return is correct.
  -> share_data/break_scorer.joblib + break_scorer_report.txt

### Result — the before-window carries only MODEST signal

```
                          break>60   flat<30   combined   AUC
unbalanced                 82%         9%        45.6%     0.727
balanced (class_weight)    53%        19%        35.9%     0.718
```

AUC ~0.72 is the CEILING; class-reweighting only SLIDES the operating point, it adds no
information. At 0.72 the two distributions overlap heavily, so you CANNOT hit both
"break>60" and "flat<30" at once (that would need AUC ~0.85+). The 0.72 (> 0.5 coin-flip)
says there IS a real edge in the before-window — but it is weak: the turn is genuinely
hard to call BEFORE it fires.

Logistic readout: by far the strongest tell is the DECISION-DAY return (r19), NEGATIVE — a
quiet/down last day predicts a turn, a hot last day predicts a flat (weak buy-the-dip).
Everything else is small.

mcap "where are the fish" (generic +10% bar): mid-large 35-80B best (1.1x), smallest
0.7-8.4B worst (0.9x) — the "small caps move more" intuition is NOT supported here. mcap_b
is carried as a feature/output so pre_break can report each pick's cap band.

### Next levers (NOT done — J to pick)

1. Add engineered features (mom20, dist-from-250d-high, vol_ratio20, MA-aligned,
   range-contraction) on top of the raw sequence — attacks the AUC ceiling with features
   winner_study already PROVED carry signal. RECOMMENDED first move.
2. Lengthen the before-window (period 40/60) for more context per sample.
3. Accept the 0.72 ceiling; pick ONE operating point (favour flat-rejection OR break-recall).
4. Accept that the turn may not be pre-visible from price/volume alone -> this line is done.

The current saved model (balanced GB) does NOT meet the 60/30 goal — do not wire it into
live picking yet.

---

1. ADD ENGINEERED FEATURES   <- highest leverage, and we've NEVER tried it
   The model currently sees only raw daily returns + volume ratios. It has to
   REINVENT the things winner_study already PROVED predictive:
     mom20, distance-from-250d(52wk)-high, vol_ratio20, MA 5>10>20 aligned,
     range contraction, turnover.
   These are aggregate/nonlinear — raw dailies can't easily reconstruct them.
   This is THE untried lever and the most likely to add real AUC.

2. KEEP WINDOW SHORT (20, test 10-15) + ADD 2024 BACK
   Short window = less noise. Re-including 2024 ~doubles training data ->
   less overfit -> better held-out AUC. (We deleted 2024 earlier; bring it back.)

3. SHARPEN THE BAR (+20%)
   +10->+15 already lifted 0.72->0.80. +20% may add more (sharper fingerprint),
   but fewer samples -> watch the train/test gap for overfit.

4. MODEL HYPERPARAMS (depth/leaves/L2) — last, ~0.01-0.02 only.
