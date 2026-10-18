"""Spreadsheets: CSV and XLSX rows become records; totals, filters, group-by and counts are exact.

Run: python -I -X utf8 tests/test_tables.py   (from C:\\Users\\lucas\\mdtree)
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import facts, tables  # noqa: E402
from engine.ingest import add_file  # noqa: E402
from engine.llm import FakeLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

CSV = """Invoice_Number,Customer,Customer_Address,Invoice_Date,Due_Date,Subtotal_AUD,GST_AUD,Total_AUD,Status
INV-2001,Smith Family,"1 Hill St, Brisbane",2026-01-10,2026-02-09,1000.00,100.00,1100.00,Paid
INV-2002,Lee Cafe,"2 Main Rd, Brisbane",2026-01-20,2026-02-19,2000.00,200.00,2200.00,Unpaid
INV-2003,Smith Family,"1 Hill St, Brisbane",2026-02-05,2026-03-07,500.00,50.00,550.00,Unpaid
INV-2004,Jones Plumbing,"3 Bay Ave, Brisbane",2026-02-15,2026-03-17,3000.00,300.00,3300.00,Written Off
INV-2005,Lee Cafe,"2 Main Rd, Brisbane",2026-03-01,2026-03-31,1500.00,150.00,1650.00,Unpaid
"""

failures = []


def check(name, cond, detail=""):
    print("PASS" if cond else "FAIL", name, detail)
    if not cond:
        failures.append(name)


def run(lib, label):
    s = Searcher(lib, FakeLLM())
    r = s.ask("How much is unpaid in total?")
    check(f"{label} unpaid total", "$4,400" in r["answer"] and "3 rows" in r["answer"], r["answer"])
    r = s.ask("What is the total GST?")
    check(f"{label} GST column total", "$800" in r["answer"], r["answer"])
    r = s.ask("How many invoices are written off?")
    check(f"{label} count", r["answer"] == "Matching: 1 row.", r["answer"])
    r = s.ask("Chart unpaid totals by customer")
    pts = {p["x"]: p["value"] for p in r["chart"]["points"]} if r.get("chart") else {}
    check(f"{label} group by customer", pts == {"Lee Cafe": 3850.0, "Smith Family": 550.0}, str(pts))
    r = s.ask("List unpaid invoices from February 2026")
    names = [row[0] for row in r["table"]["rows"]] if r.get("table") else []
    check(f"{label} list with window", names == ["Invoices · row 3"], str(names))


def main():
    header, rows = tables.read_table("x.csv", CSV.encode())
    rl = tables.roles(header, rows)
    check("roles", [header[i] for i in rl["money"]] == ["Subtotal_AUD", "GST_AUD", "Total_AUD"]
          and header[rl["party"]] == "Customer" and header[rl["ref"]] == "Invoice_Number"
          and header[rl["dates"][0]] == "Invoice_Date", str(rl))
    check("formula-safe numbers", tables.to_number("(1,200.50)") == -1200.5 and tables.to_number("abc") is None)

    root = tempfile.mkdtemp(prefix="mhizva_t_")
    try:
        lib = Library(root)
        r = add_file(lib, FakeLLM(), "Invoices.csv", CSV.encode())
        check("filed under Invoices", r["path"].startswith("Invoices/"), r["path"])
        check("facts per money cell", len(facts.FactStore(root).all()) == 15)
        run(lib, "csv")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # the same sheet as .xlsx (written by our own exporter, read back by the importer)
    table = {"columns": [{"name": h, "type": "text"} for h in header], "rows": rows, "text": rows}
    xlsx = facts.to_xlsx(table)
    h2, r2 = tables.read_table("Invoices.xlsx", xlsx)
    check("xlsx round trip", h2 == header and len(r2) == 5 and r2[1][1] == "Lee Cafe", str(r2[:1]))
    root = tempfile.mkdtemp(prefix="mhizva_t_")
    try:
        lib = Library(root)
        add_file(lib, FakeLLM(), "Invoices.xlsx", xlsx)
        run(lib, "xlsx")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(failures)} failures")
    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
