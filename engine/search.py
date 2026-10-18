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
from .llm import chat, has_cjk, words

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
COUNTRY = "Australia"  # where the business is; general answers assume it (becomes a setting later)
MODES = ("auto", "general", "data", "business")


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


OWN_RECORDS = re.compile(r"\b(our|we|us|my|mine|company|business|staff|client|customer|supplier|invoice|"
                         r"quote|contract|policy|bill|receipt)s?\b|我们|我的|公司|员工|客户|供应商|发票|合同|账单", re.I)


ASKING = re.compile(r"\?|？|^\s*(what|when|where|who|whom|whose|which|why|how|is|are|was|were|do|does|did|can|could|"
                    r"should|will|would|list|show|chart|graph|plot|find|give|tell|total|sum|compare|explain|summari[sz]e)\b"
                    r"|吗|什么|多少|几|哪|怎么|怎样|为什么|谁|列出|画|显示|告诉|解释|比较|总结", re.I)
CHAT_REPLY = {"en": "Nice to meet you! I'm Mhizva. Ask me anything about your documents, like totals, "
                    "due dates or trends, and I'll show you where the answer came from.",
              "zh": "很高兴认识你！我是 Mhizva。你可以问我关于你文件的任何问题，比如合计、到期日或趋势，我会告诉你答案出自哪里。"}


CHAT = re.compile(r"^\s*(hi|hello|hey|yo|thanks|thank you|cheers|bye|goodbye|good (morning|afternoon|evening|night)|"
                  r"ok|okay|cool|nice|great|lol)\b|\b(i|we)\s+(\w+\s+)?(created|made|built|love|like|hate)\s+(you|u)\b|"
                  r"\b(i am|i'm|im)\s+(ur|your)\b|\b(you|u)('re| are| r)\b|你好|您好|谢谢|再见|我(创造|做|造)了你|你是", re.I)


def chit_chat(question):
    """Greetings, thanks and remarks about Mhizva ('i created you') are not questions: answer with a fixed
    friendly reply instead of handing a small model nothing to do (it tends to recite its instructions).
    Keyword searches without a question mark ('Smith contract price') still search."""
    return bool(CHAT.search(question)) and not ASKING.search(question.strip())


HISTORY_TURNS, HISTORY_CHARS = 6, 1500  # fits the 4k context with room for the reply


def clean_history(history):
    """Prior General-mode turns only: roles checked, newest kept, total size capped."""
    out, size = [], 0
    for h in reversed(history or []):
        if not isinstance(h, dict) or h.get("role") not in ("user", "assistant"):
            continue
        content = str(h.get("content") or "").strip()[:500]
        if not content or size + len(content) > HISTORY_CHARS or len(out) >= HISTORY_TURNS:
            break
        out.append({"role": h["role"], "content": content})
        size += len(content)
    return list(reversed(out))


def leaked(answer, instructions):
    """True when a general answer mostly repeats its hidden instructions."""
    a, i = set(words(answer)), set(words(instructions))
    return bool(a) and len(a & i) / len(a) > 0.5


def grounded(answer, question, quotes):
    """Small models sometimes answer from memory instead of the quotes ("GST ... in Canada").
    Accept an answer only if most of its own content words (not the question's) appear in the quotes,
    and every number in it does."""
    nums = re.findall(r"\d[\d,.]*", answer)
    if any(n.strip(".,") not in quotes for n in nums):
        return False
    new = set(words(answer)) - set(words(question))
    if not new:
        return True
    have = set(words(quotes))
    # Chinese terms are checked as text: a lone character (天) never forms a pair
    return sum(1 for w in new if w in have or (not w.isascii() and w in quotes)) / len(new) >= 0.6


def general_question(question):
    """A question about the world, not about this business's own records."""
    return not OWN_RECORDS.search(question)


class Searcher:
    def __init__(self, lib, llm):
        self.lib, self.llm = lib, llm
        self.history = []
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

    def ask(self, question, budget=4.0, mode="auto", history=None):
        """mode (Lucas): "general" = plain assistant, never touches the library; "data" = only computed
        totals/tables/charts/trends from the facts table; "business" = the documents only (lookups and
        facts), never a general-knowledge answer; "auto" = decide per question."""
        self.history = clean_history(history) if mode == "general" else []  # history only in General mode
        res = self._ask(question, budget, mode if mode in MODES else "auto")
        res["mode"] = mode if mode in MODES else "auto"
        return res

    def _ask(self, question, budget, mode):
        t0 = time.time()
        key = mode + "|" + " ".join(question.lower().split())  # whole question: "not" must change the key
        if key in self.cache:
            return {**self.cache[key], "seconds": round(time.time() - t0, 2), "cached": True}
        if chit_chat(question):
            return self.friendly(question, t0)
        if mode == "general":
            return self.general(question, t0, [], business=False) or self.friendly(question, t0)
        kind = facts.question_kind(question)
        if mode == "data" and kind == "lookup":
            kind = "table"  # data mode never writes free text: a plain question lists the matching facts
        if mode == "data":
            res = facts.answer(facts.FactStore(self.lib.root).all(), question, kind)
            if not res:
                zh = has_cjk(question)
                return {"id": uuid.uuid4().hex[:12], "kind": kind, "found": False, "quotes": [], "table": None,
                        "chart": None, "coverage": None, "trace": [], "seconds": round(time.time() - t0, 2),
                        "answer": "没有找到相关的数字或日期。" if zh else "I couldn't find matching numbers or dates."}
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
        zh = has_cjk(question)
        answer, found = ("在你的文件里没有找到这个答案。" if zh else "I couldn't find that in your documents."), False
        if picked:
            listing = "\n".join(f"- {c[1]}" for c in picked)
            # Optimisation 4: the answer is one short sentence; the quotes carry the detail.
            text = self.llm.write(chat(
                "Answer only from the quotes. If they do not contain the answer, reply NOT_FOUND.",
                f"Quotes:\n{listing}\n\nQuestion: {question}\nAnswer in one short sentence"
                + (", in Chinese." if zh else ".")),
                n=60, stop=["<|eot_id|>", "\n"])
            if text and "NOT_FOUND" not in text and grounded(text, question, listing):
                answer, found = text, True
        kind = "lookup"
        if not found and mode == "auto" and general_question(question):
            res = self.general(question, t0, trace, business=True)
            if res:
                return res
        out = {"id": uuid.uuid4().hex[:12], "kind": kind, "answer": answer, "found": found,
               "quotes": [{"text": c[1], "path": c[2], "title": c[3]} for c in picked] if found else [],
               "table": None, "chart": None, "coverage": None, "trace": trace}
        if found:
            self.cache[key] = out
        return {**out, "seconds": round(time.time() - t0, 2)}

    def general(self, question, t0, trace, business):
        """A general-knowledge answer, clearly labelled. Never filed, cached or turned into facts, so it
        can't overwrite what the documents say. business=False (general mode) uses a neutral assistant
        with no business framing."""
        zh = has_cjk(question)
        instructions = (f"You are Mhizva, a helpful assistant for a small business in {COUNTRY}. Answer general "
                        "questions briefly from general knowledge. If it depends on the business's own records, "
                        "say you couldn't find it in their documents." if business else
                        "You are Mhizva, a friendly assistant that runs privately on this computer. Chat naturally, "
                        "answer clearly and briefly, and say so when you are not sure.")
        past = "".join(f"{'User' if h['role'] == 'user' else 'Mhizva'}: {h['content']}\n" for h in self.history)
        text = self.llm.write(chat(
            instructions,
            (f"Conversation so far:\n{past}\n" if past else "")
            + f"User: {question}\nReply in at most three sentences" + (", in Chinese." if zh else ".")),
            n=160, stop=["<|eot_id|>", "\nUser:"])
        if not text:
            return None
        if leaked(text, instructions):
            return self.friendly(question, t0)
        return {"id": uuid.uuid4().hex[:12], "kind": "general", "answer": text, "found": False,
                "general": True, "quotes": [], "table": None, "chart": None, "coverage": None,
                "trace": trace, "seconds": round(time.time() - t0, 2)}

    def friendly(self, question, t0):
        return {"id": uuid.uuid4().hex[:12], "kind": "chat", "answer": CHAT_REPLY["zh" if has_cjk(question) else "en"],
                "found": False, "quotes": [], "table": None, "chart": None, "coverage": None, "trace": [],
                "seconds": round(time.time() - t0, 2)}

    def forget(self):
        self.cache.clear()
