"""Question path: flow-based descent through the index tree, then a quoted answer.

Budget is measured in documents read (a read costs 1, a dead-end read 0.5). At each index every child
gets a score; a clear winner takes the whole budget (optimisation 5), otherwise the budget is split by
score. Budget a branch does not use flows back and on to the next-best unvisited branch. Model calls
and folder visits have their own ceilings, so a deep tree cannot run away.
"""
import re
import time
import uuid

from . import facts
from .ingest import LABELS, menu
from .llm import chat, words

CUTOFF = 0.12      # children scored below this get no budget
CLEAR_WIN = 0.6    # a child this strong takes everything
CLEAR_GAP = 0.35   # ...or one this far ahead of the runner-up
MAX_SPLIT = 4      # most branches explored from one index
QUOTE_CHARS = 500
KW_WEIGHT = 0.25   # share of the score from keyword overlap (optimisation 6)
BACKTRACK_FLOOR = 0.05  # leftover budget never flows to children scored below this
MAX_CALLS = 12     # routing calls per question (index scoring); keywords only after that.
                   # Each answer adds at most one passage pick and one short write on top.
MAX_NODES = 40     # folders visited per question


def overlap(q, text):
    t = set(words(text))
    return sum(1 for w in q if w in t) / max(1, len(q))


def chunks(text):
    """Consecutive paragraphs grouped up to QUOTE_CHARS, so a short document is one quote."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > QUOTE_CHARS:
            cut = para.rfind(". ", 0, QUOTE_CHARS) + 1 or QUOTE_CHARS
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if cur and len(cur) + len(para) + 2 > QUOTE_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}".strip() if para else cur
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= 20]


class Searcher:
    def __init__(self, lib, llm):
        self.lib, self.llm = lib, llm
        self.cache = {}  # optimisation 9: repeated questions

    def score(self, question, q, entries):
        if len(entries) > len(LABELS):  # wide folder: score in batches the model can label
            out = []
            for i in range(0, len(entries), len(LABELS)):
                batch = entries[i:i + len(LABELS)]
                # each batch's scores sum to 1; weight by batch size so a small last batch isn't inflated
                out += [x * len(batch) / len(entries) for x in self.score(question, q, batch)]
            return out
        kw = [overlap(q, f"{e.name} {e.summary} {' '.join(e.keywords)}") for e in entries]
        if len(entries) == 1:
            return [1.0]
        ranked = sorted(kw, reverse=True)
        kw_total = sum(kw) or 1.0
        if (ranked[0] >= 0.6 and ranked[1] < 0.2) or self.calls_left <= 0:
            # obvious keyword match, or the model-call ceiling is spent: keywords only
            return [k / kw_total if sum(kw) else 1.0 / len(kw) for k in kw]
        self.calls_left -= 1
        labels = list(LABELS[:len(entries)])
        # Items first, question last: the index part of the prompt is identical between questions,
        # so llama-server can reuse its cached state for it (optimisation 2).
        probs = self.llm.pick(chat(
            "You help find information in a filing system.",
            f"Items:\n{menu(entries)}\n\nQuestion: {question}\n"
            f"Which item most likely contains the answer? Answer with one letter."), labels)
        return [(1 - KW_WEIGHT) * probs[l] + KW_WEIGHT * (k / kw_total) for l, k in zip(labels, kw)]

    def spend(self, question, q, path, kind, alloc, trace, found):
        """Spend up to `alloc` on one child; return what is left. A read costs 1 (0.5 for a dead end)."""
        if kind == "folder":
            return self.descend(question, q, path, alloc, trace, found)
        if alloc < 1:
            return alloc
        _, text = self.lib.read_doc(path)
        rel = overlap(q, text)
        found.append((path, text, rel))
        return alloc - (0.5 if rel == 0 else 1.0)

    def descend(self, question, q, folder, budget, trace, found):
        """Visit `folder` with `budget`; return the unspent budget (never negative, never more than given)."""
        entries = self.lib.entries(folder)
        if not entries or budget < 1 or self.nodes_left <= 0:
            return budget
        self.nodes_left -= 1
        scores = self.score(question, q, entries)
        order = sorted(range(len(entries)), key=lambda i: -scores[i])
        top = scores[order[0]]
        second = scores[order[1]] if len(order) > 1 else 0.0
        if top >= CLEAR_WIN or top - second >= CLEAR_GAP:
            chosen = order[:1]
        else:
            chosen = [i for i in order if scores[i] >= CUTOFF][:MAX_SPLIT] or order[:1]
        total = sum(scores[i] for i in chosen)
        step = {"index": folder or "Library",
                "scores": {entries[i].name: round(scores[i], 3) for i in order},
                "chosen": [entries[i].name for i in chosen]}
        trace.append(step)

        pool, visited = budget, set()
        for k, i in enumerate(chosen):
            if pool < 1:
                break
            # each chosen branch gets its share, at least one read's worth; the last takes what is left
            want = pool if k == len(chosen) - 1 else max(1.0, budget * scores[i] / total)
            alloc = min(pool, want)
            path = f"{folder}/{entries[i].name}".lstrip("/")
            pool = pool - alloc + self.spend(question, q, path, entries[i].kind, alloc, trace, found)
            visited.add(i)
        # Backtracking: budget left over (dead ends) flows to the next-best unvisited children.
        for i in order:
            if pool < 1:
                break
            if i in visited or scores[i] < BACKTRACK_FLOOR:
                continue
            step["chosen"].append(entries[i].name)
            path = f"{folder}/{entries[i].name}".lstrip("/")
            pool = self.spend(question, q, path, entries[i].kind, pool, trace, found)
            visited.add(i)
        return pool

    def quotes(self, question, q, docs):
        cands = []
        for path, text, _ in docs:
            meta, _ = self.lib.read_doc(path)
            title = meta.get("title") or path
            for chunk in chunks(text):
                s = overlap(q, chunk)
                if s > 0:
                    cands.append([s, chunk, path, title])
        cands.sort(key=lambda c: -c[0])
        cands = cands[:6]
        if len(cands) > 1:
            labels = list(LABELS[:len(cands)])
            listing = "\n".join(f"{l}) {c[1][:300]}" for l, c in zip(labels, cands))
            probs = self.llm.pick(chat(
                "You find the passage that answers a question.",
                f"Passages:\n{listing}\n\nQuestion: {question}\nWhich passage answers it? Answer with one letter."),
                labels)
            for l, c in zip(labels, cands):
                c[0] = 0.6 * probs[l] + 0.4 * c[0]
            cands.sort(key=lambda c: -c[0])
        if not cands:
            return []
        best = cands[0][0]
        return [c for c in cands if c[0] >= 0.5 * best][:3]

    def ask(self, question, budget=4.0):
        t0 = time.time()
        key = " ".join(question.lower().split())  # whole question: "not" must change the key
        if key in self.cache:
            return {**self.cache[key], "seconds": round(time.time() - t0, 2), "cached": True}
        kind = facts.question_kind(question)
        if kind != "lookup":
            res = facts.answer(facts.FactStore(self.lib.root).all(), question, kind)
            if res:  # cross-document question answered from the facts table; otherwise fall through
                text, table, chart, cover = res
                seen, quotes = set(), []
                for src in table["sources"]:
                    s = src[0]
                    if s["path"] not in seen and len(quotes) < 5:
                        seen.add(s["path"])
                        quotes.append({"text": s["quote"], "path": s["path"],
                                       "title": self.lib.read_doc(s["path"])[0].get("title", s["path"])})
                out = {"id": uuid.uuid4().hex[:12], "kind": kind, "answer": text, "found": True,
                       "quotes": quotes, "table": table, "chart": chart, "coverage": cover, "trace": []}
                self.cache[key] = out
                return {**out, "seconds": round(time.time() - t0, 2)}
        q = set(words(question))
        trace, docs = [], []
        self.calls_left, self.nodes_left = MAX_CALLS, MAX_NODES
        self.descend(question, q, "", budget, trace, docs)
        picked = self.quotes(question, q, docs)
        answer, found = "I couldn't find that in your documents.", False
        if picked:
            listing = "\n".join(f"- {c[1]}" for c in picked)
            # Optimisation 4: the answer is one short sentence; the quotes carry the detail.
            text = self.llm.write(chat(
                "Answer only from the quotes. If they do not contain the answer, reply NOT_FOUND.",
                f"Quotes:\n{listing}\n\nQuestion: {question}\nAnswer in one short sentence."),
                n=60, stop=["<|eot_id|>", "\n"])
            if text and "NOT_FOUND" not in text:
                answer, found = text, True
        out = {"id": uuid.uuid4().hex[:12], "kind": "lookup", "answer": answer, "found": found,
               "quotes": [{"text": c[1], "path": c[2], "title": c[3]} for c in picked] if found else [],
               "table": None, "chart": None, "coverage": None, "trace": trace}
        if found:
            self.cache[key] = out
        return {**out, "seconds": round(time.time() - t0, 2)}

    def forget(self):
        self.cache.clear()
