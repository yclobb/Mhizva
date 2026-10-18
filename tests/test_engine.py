"""End-to-end engine check with the keyword stand-in model: file the samples, then ask questions.

Run: python -I tests/test_engine.py   (from C:\\Users\\lucas\\mdtree)
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from engine.ingest import add_file  # noqa: E402
from engine.llm import FakeLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

CASES = [
    ("How much is the Smith contract?", "Smith renovation contract", "42,000"),
    ("When does the ute insurance renew?", "Ute insurance", "1 July"),
    ("How much annual leave do staff get?", "Staff leave policy", "4 weeks"),
    ("What did we quote Sarah Jones for the bathroom?", "Jones bathroom quote", "10,000"),
]


def main():
    root = tempfile.mkdtemp(prefix="mdtree_test_")
    try:
        lib, llm = Library(root), FakeLLM()
        for f in sorted(os.listdir(os.path.join(HERE, "samples"))):
            with open(os.path.join(HERE, "samples", f), "rb") as fh:
                r = add_file(lib, llm, f, fh.read())
            print("filed", r["path"])
        print(open(os.path.join(root, "index.md"), encoding="utf-8").read())
        s = Searcher(lib, llm)
        fails = 0
        for q, want_title, want_text in CASES:
            r = s.ask(q)
            ok = r["found"] and r["quotes"] and want_title in r["quotes"][0]["title"] \
                and any(want_text in c["text"] for c in r["quotes"])
            fails += not ok
            print("PASS" if ok else "FAIL", q, "->", r["answer"][:80], [c["path"] for c in r["quotes"]])
        r = s.ask("What is the capital of France?")
        print("PASS" if not r["found"] else "FAIL", "unanswerable ->", r["answer"])
        fails += r["found"]
        from engine.search import grounded
        g = [grounded("GST is a tax on goods and services purchased in Canada.", "What is GST?",
                      "- The total contract price is $42,000 including GST."),
             grounded("$42,000", "How much is the Smith contract?", "- The total contract price is $42,000."),
             grounded("15天", "员工年假有几天？", "- 全职员工每年有15天带薪年假。"),
             grounded("20天", "员工年假有几天？", "- 全职员工每年有15天带薪年假。")]
        ok = g == [False, True, True, False]
        fails += not ok
        print("PASS" if ok else "FAIL", "grounding check", g)
        # identity and chit-chat (GPT's cases): fixed true answers from runtime metadata, history active
        from engine.llm import ServerLLM, TeamLLM
        s.llm = TeamLLM(ServerLLM("qwen2.5-0.5b", 1), ServerLLM("qwen2.5-1.5b", 2))  # names only, never started
        hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"}]
        seq = [("r u an ai", "qwen2.5-1.5b"), ("what model are you", "qwen2.5-0.5b and qwen2.5-1.5b"),
               ("i am ur creator", "What would you like to talk about"),
               ("no i literally made u", "What would you like to talk about"),
               ("how smart are you", "small model"), ("for your model, what are you?", "qwen2.5-1.5b"),
               ("ok. what are your limitations compared to higher quality models like claude opus 5.5 and where you u "
                "outpeform them", "never leave this computer")]
        for q, want in seq:
            r = s.ask(q, mode="general", history=hist)
            ok = want in r["answer"] and "Anthropic" not in r["answer"] and r["kind"] == "chat"
            fails += not ok
            print("PASS" if ok else "FAIL", "identity:", q, "->", r["answer"][:70])
        from engine.search import chit_chat
        not_chat = ["ok. what are the payment terms", "hi, explain GST to me", "hey show me unpaid invoices",
                    "thanks, list all bills", "ok so how much do we owe", "ok but u didnt answer my statement",
                    "u dum"]
        is_chat = ["hi", "thanks!", "ok", "hi there!", "i am ur creator", "no i literally made u", "你好"]
        ok = not any(chit_chat(q) for q in not_chat) and all(chit_chat(q) for q in is_chat)
        fails += not ok
        print("PASS" if ok else "FAIL", "chit-chat boundaries",
              [q for q in not_chat if chit_chat(q)], [q for q in is_chat if not chit_chat(q)])
        r = s.ask("hi", mode="business")
        ok = "documents" in r["answer"]
        r = s.ask("hi", mode="general")
        ok = ok and "documents" not in r["answer"]
        fails += not ok
        print("PASS" if ok else "FAIL", "mode-aware greeting", r["answer"])
        print(f"{fails} failures")
        return fails
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
