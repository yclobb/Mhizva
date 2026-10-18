"""Regression tests for the engine review findings (GPT, 2026-10-18).

Run: python -I tests/test_budget.py   (from C:\\Users\\lucas\\mdtree)
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import ingest  # noqa: E402
from engine.ingest import MAX_CHILDREN, split_sections  # noqa: E402
from engine.llm import FakeLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Entry, Library  # noqa: E402


class FixedLLM(FakeLLM):
    """Returns preset label probabilities (in label order) for every pick."""
    def __init__(self, probs):
        self.probs = probs

    def pick(self, prompt, labels):
        ps = (self.probs + [0.0] * len(labels))[:len(labels)]
        total = sum(ps) or 1.0
        return {l: p / total for l, p in zip(labels, ps)}


def lib_with(docs):
    """A library with one flat folder of docs: [(name, text)]. Summaries are neutral so keywords don't decide."""
    lib = Library(tempfile.mkdtemp(prefix="mhizva_t_"))
    for name, text in docs:
        lib.write_doc("", name, {"title": name}, text)
        lib.put_entry("", Entry(name, "doc", "a document", []))
    return lib


def reads(searcher, budget, question="what is the widget price"):
    q = set(__import__("engine.llm", fromlist=["words"]).words(question))
    searcher.calls_left, searcher.nodes_left = 12, 40
    found = []
    left = searcher.descend(question, q, "", budget, [], found)
    return [p for p, _, _ in found], left


failures = []


def check(name, cond, detail=""):
    print("PASS" if cond else "FAIL", name, detail)
    if not cond:
        failures.append(name)


def main():
    rel = "The widget price is $5."
    # 1. budget is never exceeded (GPT: budget 0.5 read one doc, 1.5 read two)
    lib = lib_with([("a.md", rel), ("b.md", rel)])
    s = Searcher(lib, FixedLLM([0.5, 0.5]))
    for budget, most in ((0.5, 0), (1.0, 1), (1.5, 1), (2.0, 2)):
        got, left = reads(s, budget)
        check(f"budget {budget} reads <= {most}", len(got) <= most and left >= 0, f"read {got}, left {left}")
    shutil.rmtree(lib.root)

    # 2. clear winner that dead-ends: leftover flows on to the runner-up
    lib = lib_with([("dead.md", "Nothing useful here at all."), ("good.md", rel)])
    s = Searcher(lib, FixedLLM([0.9, 0.1]))
    got, left = reads(s, 4.0)
    check("backtrack after dead end", got == ["dead.md", "good.md"], f"read {got}")
    shutil.rmtree(lib.root)

    # 3. a folder wider than 16 entries: entry 20 is scored and reachable
    lib = lib_with([(f"d{i:02d}.md", "filler text") for i in range(19)])
    lib.write_doc("", "target.md", {"title": "target"}, rel)
    lib.put_entry("", Entry("target.md", "doc", "widget price list", ["widget", "price"]))
    s = Searcher(lib, FakeLLM())
    trace = []
    s.calls_left, s.nodes_left = 12, 40
    found = []
    q = set(__import__("engine.llm", fromlist=["words"]).words("what is the widget price"))
    s.descend("what is the widget price", q, "", 2.0, trace, found)
    check("entry 20 scored", "target.md" in trace[0]["scores"], f"{len(trace[0]['scores'])} scored")
    check("entry 20 reached", any(p == "target.md" for p, _, _ in found), f"read {[p for p, _, _ in found]}")
    shutil.rmtree(lib.root)

    # 4. one giant paragraph is cut into SECTION-sized sections with no text lost
    big = ("Sentence number one about invoices. " * 600).strip()
    secs = split_sections(big)
    check("giant paragraph split", max(len(x) for x in secs) <= ingest.SECTION + 2 and len(secs) > 1,
          f"{len(secs)} sections, max {max(len(x) for x in secs)}")
    check("no text lost", "".join(secs).replace(" ", "") == big.replace(" ", ""))

    # 5. filing keeps the root under MAX_CHILDREN folders
    lib = Library(tempfile.mkdtemp(prefix="mhizva_t_"))
    llm = FakeLLM()
    for i in range(MAX_CHILDREN + 4):
        ingest.add_file(lib, llm, f"Topic{i} unique{i}.txt", f"Unique{i} words only{i} here{i}.".encode())
    check("root fan-out capped", len(lib.entries("")) <= MAX_CHILDREN, f"{len(lib.entries(''))} root entries")
    shutil.rmtree(lib.root)

    # 6. a long document with more than 16 sections is grouped into part folders
    lib = Library(tempfile.mkdtemp(prefix="mhizva_t_"))
    long_md = "\n\n".join(f"# Section {i}\n" + "Clause text about leases. " * 120 for i in range(40))
    ingest.add_file(lib, llm, "Big lease.md", long_md.encode())
    widest = max(len(lib.entries(f)) for f in walk(lib))
    check("long doc indexes stay small", widest <= MAX_CHILDREN, f"widest index {widest}")
    shutil.rmtree(lib.root)

    # 7. a very long document (300 sections) still has no index over MAX_CHILDREN
    lib = Library(tempfile.mkdtemp(prefix="mhizva_t_"))
    secs = [f"Clause {i} text." for i in range(300)]
    ingest.place_sections(lib, llm, ingest.summarise_folder(llm), "Huge", "Huge", {}, "s", [], secs, 0)
    widest = max(len(lib.entries(f)) for f in walk(lib, "Huge"))
    check("300 sections stay bounded", widest <= MAX_CHILDREN, f"widest index {widest}")
    check("300 sections all filed", count_docs(lib, "Huge") == 300, f"{count_docs(lib, 'Huge')} docs")
    shutil.rmtree(lib.root)

    # 8. the answer cache must not merge a question with its negation (GPT)
    lib = lib_with([("leave.md", "Staff are eligible for leave after 6 months.")])
    s = Searcher(lib, FakeLLM())
    s.ask("Which staff are eligible for leave?")
    r = s.ask("Which staff are not eligible for leave?")
    check("negation not served from cache", not r.get("cached"), str(r.get("cached")))
    shutil.rmtree(lib.root)

    print(f"{len(failures)} failures")
    return len(failures)


def count_docs(lib, folder):
    return sum(count_docs(lib, f"{folder}/{e.name}") if e.kind == "folder" else 1 for e in lib.entries(folder))


def walk(lib, folder=""):
    yield folder
    for e in lib.entries(folder):
        if e.kind == "folder":
            yield from walk(lib, f"{folder}/{e.name}".lstrip("/"))


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
