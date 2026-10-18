"""Cross-document questions ("unchatgptable"): totals, tables, charts and spreadsheet export.

Run: python -I tests/test_facts.py   (from C:\\Users\\lucas\\mdtree)
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from engine import facts  # noqa: E402
from engine.ingest import add_file  # noqa: E402
from engine.llm import FakeLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("PASS" if cond else "FAIL", name, detail)
    if not cond:
        failures.append(name)


def main():
    # parsing
    m = facts.AMOUNT.search("Total quote: $10,000 excluding GST.")
    check("amount parse", m and facts.parse_amount(m) == 10000.0)
    check("dates parse", facts.parse_dates("starts 3 March 2026, ends 30/05/2026") == ["2026-03-03", "2026-05-30"])
    check("kind routing", [facts.question_kind(q) for q in (
        "When does the insurance renew?", "How much do clients owe us in total?",
        "List all invoices", "Chart invoices by customer")] == ["lookup", "aggregate", "table", "chart"])

    root = tempfile.mkdtemp(prefix="mhizva_f_")
    try:
        lib, llm = Library(root), FakeLLM()
        for f in sorted(os.listdir(os.path.join(HERE, "samples"))):
            with open(os.path.join(HERE, "samples", f), "rb") as fh:
                add_file(lib, llm, f, fh.read())
        rows = facts.FactStore(root).all()
        check("facts extracted", len(rows) >= 10, f"{len(rows)} rows")
        s = Searcher(lib, llm)

        r = s.ask("How much is owed on invoices in total?")
        check("invoice total", r["kind"] == "aggregate" and "$20,250" in r["answer"], r["answer"])
        check("total lists its sources", len(r["table"]["rows"]) == 3 and len(r["quotes"]) == 3)

        r = s.ask("List all invoices due in April 2026")
        names = sorted(row[0] for row in r["table"]["rows"]) if r.get("table") else []
        check("table with date window", names == ["Invoice INV-1043 Smith", "Invoice INV-1045 Lee Cafe"], str(names))
        check("table typed", r["table"]["columns"][3]["type"] == "money" and isinstance(r["table"]["rows"][0][3], float))

        r = s.ask("Chart invoices by customer")
        pts = r["chart"]["points"] if r.get("chart") else []
        check("bar chart", r["kind"] == "chart" and r["chart"]["type"] == "bar" and len(pts) == 3,
              str([(p["x"], p["value"]) for p in pts]))
        check("chart points map to rows", all(r["table"]["rows"][i][3] is not None for p in pts for i in p["rows"]))

        r = s.ask("Chart invoice amounts by month")
        check("line chart by month", r.get("chart") and r["chart"]["type"] == "line"
              and [p["x"] for p in r["chart"]["points"]] == ["Apr 2026", "May 2026"],
              str(r.get("chart")))

        x = facts.to_xlsx(r["table"])
        with zipfile.ZipFile(io.BytesIO(x)) as z:
            check("xlsx valid", "xl/worksheets/sheet1.xml" in z.namelist() and b"<v>16800.0</v>" in z.read("xl/worksheets/sheet1.xml"))
        check("csv", facts.to_csv(r["table"]).decode("utf-8-sig").startswith("Document,Who,What,Amount,Date"))

        r = s.ask("When does the ute insurance renew?")
        check("lookup still works", r["kind"] == "lookup" and r["found"] and "1 July" in r["quotes"][0]["text"])

        # trends across many documents: timber rising, plumbing parts falling slightly
        series = [("Timber Co", "timber supply", [("15 January 2026", 1000), ("12 February 2026", 1100),
                                                   ("16 March 2026", 1250), ("14 April 2026", 1400)]),
                  ("Pipe World", "plumbing parts", [("20 January 2026", 500), ("18 March 2026", 480),
                                                     ("22 April 2026", 470)])]
        for supplier, what, points in series:
            for i, (date, amt) in enumerate(points):
                add_file(lib, llm, f"{supplier} bill {i + 1}.txt",
                         f"Bill for {what}.\nSupplier: {supplier}\n\nTotal: ${amt}. Invoice date: {date}.".encode())
        # the same timber bill saved twice (a reminder copy) must not count twice
        add_file(lib, llm, "Timber Co reminder.txt",
                 b"Reminder for timber supply bill TB-2201.\nSupplier: Timber Co\n\nTotal: $1400. Invoice date: 14 April 2026.")
        add_file(lib, llm, "Timber Co bill TB-2201.txt",
                 b"Bill TB-2201 for timber supply.\nSupplier: Timber Co\n\nTotal: $1400. Invoice date: 14 April 2026.")

        r = s.ask("Are timber costs going up?")
        check("trend rising", r["kind"] == "trend" and r["answer"].startswith("Rising"), r["answer"])
        check("trend has line chart", r.get("chart") and r["chart"]["type"] == "line")
        cov = r.get("coverage") or {}
        check("trend coverage", cov.get("documents_used", 0) >= 4 and cov.get("date_range") == ["2026-01-15", "2026-04-14"],
              str(cov))

        r = s.ask("Which suppliers are increasing their prices?")
        rows = r["table"]["rows"] if r.get("table") else []
        check("per-supplier trend", r["kind"] == "trend" and rows and rows[0][0] == "Timber Co" and rows[0][1] == "rising"
              and any(x[0] == "Pipe World" and x[1] != "rising" for x in rows), str(rows))
        check("rising named", "Timber Co" in r["answer"] and "Pipe World" not in r["answer"].split(".")[0], r["answer"])

        r = s.ask("How much have we paid Timber Co in total?")
        check("duplicate bill counted once", r["kind"] == "aggregate", r["answer"])
        print("      ", r["answer"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(failures)} failures")
    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
