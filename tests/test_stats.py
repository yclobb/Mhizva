"""Statistics on a stock-price CSV: every number checked against an independent calculation.

Run: python -I -X utf8 tests/test_stats.py   (from C:\\Users\\lucas\\mdtree)
"""
import datetime as dt
import math
import os
import random
import shutil
import statistics as st
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ingest import add_file  # noqa: E402
from engine.llm import FakeLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("PASS" if cond else "FAIL", name, detail)
    if not cond:
        failures.append(name)


def stock_csv():
    """300 trading days of a seeded random walk with a crash in the middle."""
    rnd = random.Random(7)
    d, price, rows = dt.date(2025, 1, 2), 100.0, []
    while len(rows) < 300:
        if d.weekday() < 5:
            shock = -0.04 if 140 <= len(rows) < 150 else 0
            price *= 1 + rnd.gauss(0.0008, 0.012) + shock
            rows.append((d.isoformat(), round(price * 0.995, 2), round(price, 2), rnd.randint(1_000_000, 3_000_000)))
        d += dt.timedelta(days=1)
    text = "Date,Open,Close,Volume\n" + "\n".join(f"{a},{b},{c},{v}" for a, b, c, v in rows)
    return text, rows


def main():
    text, rows = stock_csv()
    closes = [r[2] for r in rows]
    # independent reference numbers
    change = (closes[-1] - closes[0]) / closes[0] * 100
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        worst = min(worst, (c - peak) / peak)
    rets = [(b - a) / a for a, b in zip(closes, closes[1:])]
    vol_year = st.pstdev(rets) * math.sqrt(252) * 100
    root = tempfile.mkdtemp(prefix="mhizva_s_")
    try:
        lib = Library(root)
        add_file(lib, FakeLLM(), "ACME share price.csv", text.encode())
        s = Searcher(lib, FakeLLM())
        r = s.ask("Analyse the closing price")
        table = dict(map(tuple, r["table"]["rows"])) if r.get("table") else {}
        check("routed to stats", r["kind"] == "stats", r["kind"])
        check("measure is Close", r["answer"].startswith("Close:"), r["answer"])
        check("change", f"{change:+.1f}%" in table.get("Change", ""), f"{table.get('Change')} vs {change:+.1f}%")
        check("drawdown", f"{worst * 100:.1f}%" in table.get("Largest fall from a peak", ""),
              f"{table.get('Largest fall from a peak')} vs {worst * 100:.1f}%")
        check("annual volatility", f"{vol_year:.1f}% a year" in table.get("Volatility", ""),
              f"{table.get('Volatility')} vs {vol_year:.1f}%")
        check("daily spacing", "one per day" in table.get("Points", ""), table.get("Points"))
        check("moving averages", "20-day average" in table and "50-day average" in table)
        check("chart line", r.get("chart") and r["chart"]["type"] == "line" and len(r["chart"]["points"]) <= 241)
        check("no currency symbol on prices", "$" not in table.get("Last", "x"), table.get("Last"))
        r = s.ask("What is the volatility of the volume?")
        check("other measure picked", r["answer"].startswith("Volume:"), r["answer"][:40])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(failures)} failures")
    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
