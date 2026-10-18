"""Filters (period, due/paid dates, status, overdue days, amount), rank and compare, on a small ledger.

Run: python -I -X utf8 tests/test_filters.py   (from C:\\Users\\lucas\\mdtree)
"""
import datetime as dt
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import facts, filters  # noqa: E402
from engine.ingest import add_file  # noqa: E402
from engine.llm import FakeLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

failures = []
T = dt.date.today()
D = lambda days: (T + dt.timedelta(days=days)).isoformat()


def check(name, cond, detail=""):
    print("PASS" if cond else "FAIL", name, detail)
    if not cond:
        failures.append(name)


INVOICES = f"""Invoice_Number,Customer,Invoice_Date,Due_Date,Total_AUD,Status
INV-1,Smith Family,{D(-60)},{D(-45)},1000,Unpaid
INV-2,Lee Cafe,{D(-40)},{D(-10)},2500,Unpaid
INV-3,Smith Family,{D(-5)},{D(2)},400,Unpaid
INV-4,Jones Plumbing,{D(-20)},{D(-5)},3000,Paid
INV-5,Lee Cafe,{D(-1)},{D(25)},800,Unpaid
"""
BILLS = f"""Bill_Number,Supplier,Bill_Date,Due_Date,Total_AUD,Status
B-1,Timber Co,{D(-30)},{D(1)},1200,Unpaid
B-2,Pipe World,{D(-15)},{D(-2)},300,Paid
"""


def main():
    check("window this_month", filters.window("this_month", dt.date(2026, 10, 18)) == ("2026-10-01", "2026-10-31"))
    check("window last_quarter", filters.window("last_quarter", dt.date(2026, 2, 1)) == ("2025-10-01", "2025-12-31"))
    check("window 2026-Q2", filters.window("2026-Q2") == ("2026-04-01", "2026-06-30"))
    root = tempfile.mkdtemp(prefix="mhizva_f_")
    try:
        lib = Library(root)
        add_file(lib, FakeLLM(), "Invoices.csv", INVOICES.encode())
        add_file(lib, FakeLLM(), "Bills.csv", BILLS.encode())
        rows = facts.FactStore(root).all()
        ask = lambda q, kind, form: facts.answer(rows, q, kind, form)

        r = ask("total unpaid", "aggregate", {"direction": "in", "filters": {"status": "unpaid"}})
        check("status unpaid", r and "$4,700" in r[0], r and r[0])
        r = ask("total overdue", "aggregate", {"direction": "in", "filters": {"status": "overdue"}})
        check("status overdue (due date passed, unpaid)", r and "$3,500" in r[0], r and r[0])
        r = ask("more than 30 days late", "table", {"direction": "in", "filters": {"days_overdue": ">30"}})
        names = [x[1] for x in r[1]["rows"]] if r else []
        check("days overdue > 30", names == ["Smith Family"], str(names))
        r = ask("bills due this week", "table", {"direction": "out", "filters": {"period": "this_week", "date_field": "due"}})
        wk = filters.window("this_week")
        want = sorted(n for n, due in (("Timber Co", D(1)), ("Pipe World", D(-2))) if wk[0] <= due <= wk[1])
        names = sorted(x[1] for x in r[1]["rows"]) if r else []
        check("due-date period", names == want, f"{names} want {want}")
        r = ask("invoices over 1000", "table", {"direction": "in", "filters": {"amount": ">1000"}})
        names = sorted(x[1] for x in r[1]["rows"]) if r else []
        check("amount filter", names == ["Jones Plumbing", "Lee Cafe"], str(names))

        r = ask("which clients owe me the most", "rank", {"direction": "in", "group_by": "customer",
                                                           "filters": {"status": "unpaid"}})
        check("rank by customer", r and r[0].startswith("Top 2: Lee Cafe $3,300; Smith Family $1,400"), r and r[0])
        r = ask("did we make more than we spent", "compare", {"direction": "net"})
        check("compare in vs out", r and "Money in $7,700, money out $1,500, net $6,200" in r[0], r and r[0])
        r = ask("what is my cash balance", "aggregate", {"direction": "net"})
        check("cash needs bank data, said plainly", r and "bank" in r[0], r and r[0])

        s = Searcher(lib, FakeLLM())  # stand-in: the keyword rules pick rank / compare
        r = s.ask("Which customers owe the most?")
        check("rules route rank", r["kind"] == "rank", r["kind"] + " " + r["answer"][:60])
        r = s.ask("compare money in vs money out")
        check("rules route compare", r["kind"] == "compare" and "net" in r["answer"], r["answer"][:80])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(failures)} failures")
    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
