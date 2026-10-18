"""Split B1-docs.jsonl into files, check every document's arithmetic, then run Mhizva on the whole business
and compare its answers with the manifest's answer key. The first end-to-end score on known-true data.

Run: python -I -X utf8 training/businesses/load_b1.py          (stand-in model: tests filing + facts + filters)
     python -I -X utf8 training/businesses/load_b1.py --model   (real 0.5B + 4B team; slower)
"""
import json
import os
import re
import shutil
import sys
import tempfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from engine import facts  # noqa: E402
from engine.ingest import add_file  # noqa: E402
from engine.llm import FakeLLM, ServerLLM, TeamLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

B1 = os.path.join(HERE, "B1")
TODAY = "2026-10-18"


def split():
    """B1-docs.jsonl, then any B1-docs-fix*.jsonl on top (a re-sent file replaces the earlier version)."""
    import glob
    n = 0
    lines = []
    for src in [os.path.join(HERE, "B1-docs.jsonl")] + sorted(glob.glob(os.path.join(HERE, "B1-docs-fix*.jsonl"))):
        lines += open(src, encoding="utf-8").read().splitlines()
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        o = json.loads(line)
        path = os.path.join(HERE, o["file"].replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(o["content"])
        n += 1
    return n


def money(s):
    return Decimal(s.replace(",", "").replace("$", "").strip())


def check_arithmetic():
    """Line items sum to the subtotal, subtotal + GST = total, GST = total / 11."""
    bad = []
    for name in sorted(os.listdir(B1)):
        if not name.endswith(".md") or name.startswith(("CON-", "POL-")):
            continue
        text = open(os.path.join(B1, name), encoding="utf-8").read()
        lines = [money(m) for m in re.findall(r"^\|[^|\n]*\|(?:[^|\n]*\|){0,2}\s*(-?[\d,]+\.\d\d)\s*\|\s*$", text, re.M)]
        sub = re.search(r"Subtotal \(ex GST\):\*\*\s*(-?\$?[\d,.-]+)", text)
        gst = re.search(r"GST 10%:\*\*\s*(-?\$?[\d,.-]+)", text)
        tot = re.search(r"Total (?:credit )?\(inc GST\):\*\*\s*(-?\$?[\d,.-]+)", text)
        if not (sub and gst and tot):
            bad.append(f"{name}: totals not found")
            continue
        s, g, t = (money(x.group(1).replace("-$", "-")) for x in (sub, gst, tot))
        if lines and sum(lines) != s:
            bad.append(f"{name}: lines {sum(lines)} != subtotal {s}")
        if s + g != t:
            bad.append(f"{name}: {s} + {g} != {t}")
        if (t / 11).quantize(Decimal("0.01")) != g:
            bad.append(f"{name}: GST {g} != total/11 {(t / 11):.2f}")
    return bad


# question -> (answer-key name, how to read the expected value)
QUESTIONS = [
    ("How much do customers owe us in total?", "outstanding_receivables_deduped"),
    ("What is the total of overdue invoices?", "overdue_receivables_deduped"),
    ("How much does Kestrel owe us?", "kestrel_outstanding_deduped"),
    ("What's the total of unpaid bills?", "unpaid_bills_total"),
    ("How much do we owe in overdue bills?", "overdue_bills_total"),
    ("How much do we owe Vantage?", "vantage_unpaid_both_spellings"),
    ("Total of pending quotes?", "pending_quotes_total"),
    ("How many invoices are more than 30 days overdue?", "invoices_over_30_days"),
    ("How many bills are more than 30 days overdue?", "bills_over_30_days"),
    ("Which invoices are due this week?", "invoices_due_this_week"),
    ("What did we pay in rent in BAS-Q1?", "rent_q1fy27_inc_gst"),
    ("When is the lease renewal notice due?", "lease_notice_deadline"),
    ("When does the liability insurance renew?", "policy_renewal_in_days"),
]


def numbers_in(text):
    return [Decimal(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)]


def main():
    if not os.path.exists(os.path.join(HERE, "B1-docs.jsonl")):
        print("Save the 51 JSON lines as training/businesses/B1-docs.jsonl first.")
        return 1
    print(f"split {split()} files into {B1}")
    bad = check_arithmetic()
    print(f"arithmetic: {'all documents consistent' if not bad else str(len(bad)) + ' problems'}")
    for b in bad:
        print("  ", b)
    manifest = json.load(open(os.path.join(B1, "manifest.json"), encoding="utf-8"))
    key = manifest["answer_key"]

    use_model = "--model" in sys.argv
    llm = TeamLLM(ServerLLM("qwen2.5-0.5b", 8786), ServerLLM("qwen3-4b", 8787)) if use_model else FakeLLM()
    if use_model:
        llm.start()
    root = tempfile.mkdtemp(prefix="mhizva_b1_")
    try:
        lib = Library(root)
        for name in sorted(os.listdir(B1)):
            if name == "manifest.json":
                continue
            with open(os.path.join(B1, name), "rb") as f:
                add_file(lib, llm, name, f.read())
        s = Searcher(lib, llm)
        import engine.filters as F
        import datetime as dt
        F_today = dt.date.fromisoformat(TODAY)
        orig = F.window
        F.window = lambda p, today=None, field=None: orig(p, today or F_today, field)  # the spec's "today"
        ok = 0
        for q, k in QUESTIONS:
            want = key[k]
            r = s.ask(q)
            nums = numbers_in(r["answer"])
            if isinstance(want, (int, float)):
                hit = Decimal(str(want)) in nums or any(abs(n - Decimal(str(want))) < Decimal("0.01") for n in nums)
            else:
                d = dt.date.fromisoformat(want)
                hit = want in r["answer"] or f"{d.day} {d.strftime('%b')}" in r["answer"] or \
                    any(want in q_["text"] for q_ in r.get("quotes", []))
            ok += hit
            print(f"{'PASS' if hit else 'FAIL'} [{r['kind']}] {q}\n      want {want}  got: {r['answer'][:110]}")
        print(f"\nB1 answer key: {ok}/{len(QUESTIONS)}")
    finally:
        if use_model:
            llm.stop()
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
