"""Model trial: file the demo documents and ask the same questions with each model setup.
Measures upload time, answer time, accuracy and RAM of the model servers.

Run: python -I -X utf8 tests/bench_models.py qwen2.5-1.5b            (one model)
     python -I -X utf8 tests/bench_models.py team:qwen2.5-0.5b,qwen2.5-1.5b
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.ingest import add_file  # noqa: E402
from engine.llm import ServerLLM, TeamLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

# (question, text that must appear in the answer or top quote; None = should NOT be found)
QUESTIONS = [
    ("When does the ute insurance renew?", "1 July"),
    ("Who is the insurer for the ute?", "AAMI"),
    ("How much is the Smith contract?", "42,000"),
    ("How much annual leave do staff get?", "4 weeks"),
    ("What did we quote Sarah Jones for the bathroom?", "10,000"),
    ("When is the Lee Cafe invoice due?", "20 April"),
    ("员工年假有几天？", "15"),
    ("How much is owed on invoices in total?", "20,250"),
    ("Which suppliers are increasing their prices?", "Timber Co"),
    ("发票一共要收多少钱？", "11,700"),
    ("What is our office wifi password?", None),
]


def ram_mb(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    try:
        return int(out.strip().split('","')[-1].strip('"').replace(",", "").replace(" K", "")) // 1024
    except (ValueError, IndexError):
        return None


def make(setup):
    if setup.startswith("team:"):
        small, large = setup[5:].split(",")
        return TeamLLM(ServerLLM(small, 8776), ServerLLM(large, 8777))
    return ServerLLM(setup, 8776)


def procs(llm):
    return [m.proc for m in ((llm.small, llm.large) if isinstance(llm, TeamLLM) else (llm,))]


def main(setup):
    llm = make(setup)
    t0 = time.time()
    llm.start()
    load = time.time() - t0
    root = tempfile.mkdtemp(prefix="mhizva_bench_")
    result = {"setup": setup, "load_s": round(load, 1)}
    try:
        lib = Library(root)
        docs = sorted(os.listdir(os.path.join(ROOT, "demo_docs")))
        t0 = time.time()
        for f in docs:
            with open(os.path.join(ROOT, "demo_docs", f), "rb") as fh:
                add_file(lib, llm, f, fh.read())
        result["upload_s_per_doc"] = round((time.time() - t0) / len(docs), 1)
        s = Searcher(lib, llm)
        rows, correct, times = [], 0, []
        for q, want in QUESTIONS:
            t0 = time.time()
            r = s.ask(q)
            dt = time.time() - t0
            times.append(dt)
            top = r["quotes"][0]["text"] if r["quotes"] else ""
            ok = (not r["found"]) if want is None else (r["found"] and (want in r["answer"] or want in top))
            correct += ok
            rows.append({"q": q, "ok": ok, "s": round(dt, 1), "kind": r["kind"], "answer": r["answer"][:120]})
        result.update({"accuracy": f"{correct}/{len(QUESTIONS)}", "answer_s_avg": round(sum(times) / len(times), 1),
                       "answer_s_max": round(max(times), 1),
                       "ram_mb": [ram_mb(p.pid) for p in procs(llm)],
                       "escalations": getattr(llm, "escalations", None), "questions": rows})
    finally:
        llm.stop()
        shutil.rmtree(root, ignore_errors=True)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    with open(os.path.join(HERE, f"bench_{setup.replace(':', '_').replace(',', '+')}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-1.5b")
