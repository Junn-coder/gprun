#!/usr/bin/env python3
"""
grade_6test.py — forward-return grader for pre_break picks (companion to pre_break.py).

Reads Part 1 (Names) of c/6test.md (written by pre_break.py), then for each of the
6 picks computes the net price change over a 10 trading-day hold:

    entry = next trading day's OPEN after the scan date          (T1)
    exit  = CLOSE of the 10th trading day of the hold (T1 + 9)    (T10)
    net%  = (exit_close - entry_open) / entry_open * 100

Writes the results into Part 2 (Results), OVERWRITING only Part 2 and leaving
Part 1 (Names) untouched. All data is local (stock_history_ak/<code>.csv); no network.

Usage:
    python grade_6test.py
"""

import os
import re
import sys

import pandas as pd

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TOOL_DIR)
HISTORY_DIR = os.path.join(TOOL_DIR, "stock_history_ak")
SIXTEST_PATH = os.path.join(ROOT_DIR, "6test.md")

PART2_MARKER = "## 2 Results"   # must match pre_break.py
HOLD_DAYS = 10                  # trading days held (entry day inclusive)

_CODE_RE = re.compile(r"^\d{6}$")
_DATE_RE = re.compile(r"Scan date:\s*(\d{4}-\d{2}-\d{2})")


def grade_one(code: str, scan_date: str, hold: int = HOLD_DAYS):
    """Net % over a `hold`-trading-day window. Returns (net_pct, None) on success,
    or (None, reason) if it cannot be graded.

    entry = next trading day's OPEN after scan_date; exit = CLOSE of the hold-th day.
    This is the single source of truth for forward returns, shared with backtest.py.
    """
    path = os.path.join(HISTORY_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return None, "no data file"
    try:
        df = pd.read_csv(path, parse_dates=["Date"]).sort_values("Date").reset_index(drop=True)
    except Exception as e:
        return None, f"read error: {e}"

    after = df[df["Date"] > pd.Timestamp(scan_date)].reset_index(drop=True)
    if len(after) < hold:
        return None, f"only {len(after)} trading day(s) after scan date"

    entry_open = after.loc[0, "Open"]              # T1 open
    exit_close = after.loc[hold - 1, "Close"]      # T<hold> close
    if pd.isna(entry_open) or pd.isna(exit_close) or entry_open <= 0:
        return None, "missing/zero price"

    return (exit_close - entry_open) / entry_open * 100, None


def parse_part1(prefix: str):
    """Extract scan date and [(code, name), ...] from the Names section."""
    m = _DATE_RE.search(prefix)
    scan_date = m.group(1) if m else None

    picks = []
    for line in prefix.splitlines():
        parts = line.split()
        if len(parts) >= 3 and _CODE_RE.match(parts[0]):
            picks.append((parts[0], " ".join(parts[1:-1])))  # code, name (score dropped)
    return scan_date, picks


def main():
    if not os.path.exists(SIXTEST_PATH):
        sys.exit(f"6test.md not found: {SIXTEST_PATH}")

    text = open(SIXTEST_PATH, encoding="utf-8").read()
    if PART2_MARKER not in text:
        sys.exit(f"marker '{PART2_MARKER}' not found — run pre_break.py --date <day> first to write Part 1")

    prefix = text.split(PART2_MARKER, 1)[0]
    scan_date, picks = parse_part1(prefix)
    if scan_date is None:
        sys.exit("could not find 'Scan date:' line in Part 1")
    if not picks:
        sys.exit("no pick lines parsed from Part 1 (Names)")

    rows = []
    for code, name in picks:
        net, err = grade_one(code, scan_date)
        rows.append((code, name, f"{net:+.1f}" if err is None else err))

    out = [
        PART2_MARKER,
        "",
        f"Scan date: {scan_date}  |  hold: {HOLD_DAYS} market days  |  "
        f"entry: next-day open, exit: day-{HOLD_DAYS} close",
        "",
        f"{'code':<8}{'name':<16}net%",
    ]
    for code, name, val in rows:
        out.append(f"{code:<8}{name:<16}{val}")
    out.append("")

    new_text = prefix.rstrip() + "\n\n" + "\n".join(out)
    open(SIXTEST_PATH, "w", encoding="utf-8").write(new_text)

    print(f"Graded {len(rows)} picks for {scan_date} -> {SIXTEST_PATH}")
    for code, name, val in rows:
        print(f"  {code}  {name}  {val}")


if __name__ == "__main__":
    main()
