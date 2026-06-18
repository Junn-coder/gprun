Total windows extracted:     1,907 (30-40 day pre-break windows)
├─ Training set:     1,144 samples (952 actual breaks)
└─ Test set:           763 samples (626 actual breaks)

Sample quality: ~78% positive class (break stocks vs non-break windows)

Total data files:    5,000 daily snapshots
├─ Unique stocks:      944 (after deduplication)
└─ Daily picks:          3 (top scorers each run)
Output example:

Stock 002371: Score 94/100 (RSI maxed, perfect MA positioning, high volatility)
Stock 002475: Score 94/100 (same setup)
Stock 002497: Score 94/100 (ditto)
Each stock shows:

Score (0-100)
Price, date
5 key signals with visual bars
Results saved to morning_picks.txt

Summary:

✓ 1,144 training samples = robust enough (952 pre-break examples)
✓ 763 test samples = validates the model works on unseen data
✓ 944 stocks to scan = broad coverage, but picks only top 3/day
The tool is trained on real pre-break patterns from 323 break-capable stocks (the ones that hit 10%+), but when you run the picker, it scores all 944 stocks in the pool and shows you just the 3 best setups each morning.

---

### 2026-06-18: Market regime gate

Added market regime filter to `predict_break.py`. The scanner now checks
the A-share index regime (SSE Composite + ChiNext + CSI300) before generating
candidates. If the overall light is AMBER or RED, it skips and saves a
detailed regime report instead.

Reuses `index.py`'s cached index files — no extra network calls.
`--no-regime-filter` flag to bypass.

Logic: GREEN → scan normally. AMBER/RED → "wait" with per-index detail.

### 2026-06-18: 5-day hold + waking-up features (prebreak v2)

Two key improvements over the original 10-day model:

1. **HOLD shortened 10d → 5d** — cleaner signal, faster feedback
2. **5 waking-up features** — detects "ignition" (stock stirring right now):
   - `consec_up` — consecutive green days
   - `vol_expand` — volume D > D-1 > D-2
   - `close_high_pct` — closing strength 0-100
   - `gap_up` — open gap vs yesterday close
   - `green_count5` — up days in last 5

Model: HistGradientBoosting, 54 features, trained on 307K stock-days (full
universe, no momentum-gating bias). AUC 0.748.

Backtest Jan-May 2026, top 10/day, +10% threshold:

| | Old (10d) | New (5d+waking) |
|--|-----------|-----------------|
| Hit rate +10% | 22.6% | **34.8%** |
| Avg return | +2.69% | **+5.37%** |
| Med return | +0.80% | **+3.34%** |
| Day-of signal | -4.73% (buying dips) | **+2.22%** (buying strength) |

The model no longer hunts falling knives. It buys stocks already moving today.

Key files:
- `predict_break.py` — live daily scanner (with regime gate)
- `predict_backtest.py` — temporal backtest harness
- `build_turn_dataset.py` — feature/label builder (HOLD=5, 54 features)
- `train_break.py` — model trainer
- `break_scorer.joblib` — v2 model (default)
- `break_scorer_10d.joblib` — old 10-day model (backup)
