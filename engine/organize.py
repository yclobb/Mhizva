"""Self-organisation (the creator's design): MCTS over header clarifications, scored by a blind smoke test,
with a learned value per kind of edit (the RL part).

  questions  each document's own generated questions (made at upload) plus one from its title; the target is
             that document. Split by a stable hash into a tuning set and a held-out set.
  score      blind smoke test: search each question with a small budget. +1 if the target is the first document
             read, +0.5 if read later, -1 if never reached; minus 0.05 per extra folder visited; plus 0.1 x the
             route's ternary value (documents that answered vs dead ends along the way).
  actions    header clarifications, never moves or deletes:
               keywords  add the words that set a folder or document apart from its siblings to its index line
               questions add a document's own example questions' key words to its index line
               summary   rewrite a folder's one-line summary so it names what is distinctive inside (model)
  MCTS       UCT over sequences of up to DEPTH actions; each simulation applies a sequence, scores the tuning
             set and restores the tree. Expansion order follows the learned values.
  RL         a value per action kind (count, mean reward) kept in <library>/.mhizva/organize.json and updated after
             every simulation, so later runs try the kinds of edit that have helped before.
  keep rule  the best sequence is applied only if the held-out score also improves; a report lists every change.
"""
import hashlib
import json
import math
import os
import random
import re
from collections import Counter

from .llm import FakeLLM, chat, words
from .tree import Entry

DEPTH = 3
UCB_C = 1.2


def all_folders(lib, folder=""):
    yield folder
    for e in lib.entries(folder):
        if e.kind == "folder":
            yield from all_folders(lib, f"{folder}/{e.name}".lstrip("/"))


def all_docs(lib):
    for f in all_folders(lib):
        for e in lib.entries(f):
            if e.kind == "doc":
                yield f, e


def questions(lib):
    """(question, target path) pairs from the documents' own generated questions and titles."""
    out = []
    for folder, e in all_docs(lib):
        path = f"{folder}/{e.name}".lstrip("/")
        meta, _ = lib.read_doc(path)
        if meta.get("rows"):  # spreadsheet sections: their questions go to the facts table, not the tree
            continue
        qs = [q for q in meta.get("questions", []) if len(q) > 8][:2]
        title = meta.get("title") or e.name[:-3]
        qs.append(f"What does the {title} say?")
        out += [(q, path) for q in qs]
    return out


def split(qs):
    tune, held = [], []
    for q, p in qs:
        (held if int(hashlib.md5(q.encode()).hexdigest(), 16) % 3 == 0 else tune).append((q, p))
    return tune, held


def score(lib, scorer, qs, budget=2.0, misses=None):
    """Mean blind-smoke-test reward over (question, target) pairs. Targets not read first go into `misses`."""
    from .search import Searcher, route_value
    if not qs:
        return 0.0
    s = Searcher(lib, scorer)
    total = 0.0
    for q, target in qs:
        trace, found = [], []
        s.calls_left, s.nodes_left, s.split, s.request_id = 6, 40, 3, None
        s.descend(q, set(words(q)), "", budget, trace, found)
        paths = [p for p, _, _ in found]
        r = 1.0 if paths[:1] == [target] else 0.5 if target in paths else -1.0
        if misses is not None and r < 1.0:
            misses[target] = misses.get(target, 0) + 1
        r -= 0.05 * max(0, len(trace) - 1)
        r += 0.1 * route_value(trace)
        total += r
    return total / len(qs)


def distinct_words(texts_in, texts_out, k=5):
    """Words common inside a group but rare in its siblings."""
    inside = Counter(w for t in texts_in for w in set(words(t)) if len(w) > 2 and not w.isdigit())
    outside = Counter(w for t in texts_out for w in set(words(t)))
    ranked = sorted(inside, key=lambda w: (-(inside[w] / (1 + outside[w])), w))
    return ranked[:k]


def subtree_text(lib, path):
    if not lib.is_folder(path):
        meta, _ = lib.read_doc(path)
        return [meta.get("title", ""), meta.get("summary", "")] + meta.get("questions", [])
    out = []
    for e in lib.entries(path):
        out += [e.name, e.summary] + subtree_text(lib, f"{path}/{e.name}")
    return out


def candidate_actions(lib):
    acts = []
    for folder in all_folders(lib):
        entries = lib.entries(folder)
        for e in entries:
            acts.append(("keywords", folder, e.name))
            if e.kind == "doc":
                acts.append(("questions", folder, e.name))
            elif len(lib.entries(f"{folder}/{e.name}".lstrip("/"))) > 1:
                acts.append(("summary", folder, e.name))
    return acts


def apply(lib, writer, action):
    """Apply one header clarification; returns a short description of the change (or None if no change)."""
    kind, folder, name = action
    entries = {e.name: e for e in lib.entries(folder)}
    e = entries.get(name)
    if not e:
        return None
    path = f"{folder}/{name}".lstrip("/")
    before = e.line()
    if kind == "keywords":
        siblings = [t for n, x in entries.items() if n != name for t in subtree_text(lib, f"{folder}/{n}".lstrip("/"))]
        add = [w for w in distinct_words(subtree_text(lib, path), siblings) if w not in e.keywords]
        if not add:
            return None
        new = Entry(e.name, e.kind, e.summary, (add + e.keywords)[:10])
    elif kind == "questions":
        meta, _ = lib.read_doc(path)
        add = [w for q in meta.get("questions", []) for w in words(q)
               if len(w) > 3 and w not in e.keywords and w not in set(words(e.summary))]
        add = list(dict.fromkeys(add))[:4]
        if not add:
            return None
        new = Entry(e.name, e.kind, e.summary, (add + e.keywords)[:10])
    else:  # summary
        child = "\n".join(f"- {x.name}: {x.summary}" for x in lib.entries(path)[:16])
        text = writer.write(chat(
            "You write one-line catalogue entries that help someone find the right folder.",
            f"Folder: {name}\nContents:\n{child}\n\nWrite one line (max 25 words) naming the distinctive people, "
            f"companies, document types and dates inside."), n=60, stop=["<|eot_id|>", "\n"])
        if not text or text.strip() == e.summary.strip():
            return None
        new = Entry(e.name, e.kind, text.strip(), e.keywords)
    lib.put_entry(folder, new)
    return {"where": path, "kind": kind, "before": before, "after": new.line()}


def snapshot(lib):
    return {f: open(lib._p(f, "index.md"), encoding="utf-8").read() for f in all_folders(lib)}


def restore(lib, snap):
    for f, text in snap.items():
        with open(lib._p(f, "index.md"), "w", encoding="utf-8") as fh:
            fh.write(text)


class Node:
    def __init__(self, seq):
        self.seq, self.children, self.n, self.value = seq, {}, 0, 0.0


def stats_path(lib):
    d = os.path.join(lib.root, ".mhizva")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "organize.json")


def load_stats(lib):
    try:
        return json.load(open(stats_path(lib), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_stats(lib, st):
    json.dump(st, open(stats_path(lib), "w", encoding="utf-8"), indent=1)


def organize(lib, writer=None, scorer=None, iterations=30, seed=0):
    """Run MCTS over header clarifications. Returns a report; changes are kept only if held-out improves."""
    writer = writer or FakeLLM()
    scorer = scorer or FakeLLM()  # keyword scoring by default: fast; pass a model to score with real picks
    rnd = random.Random(seed)
    qs = questions(lib)
    tune, held = split(qs)
    misses = {}
    base_tune, base_held = score(lib, scorer, tune, misses=misses), score(lib, scorer, held)
    acts = candidate_actions(lib)
    learned = load_stats(lib)  # RL: value per action kind across runs

    def on_failing_route(a):
        """How many missed questions pass through this header (it is on the path to their target)."""
        where = f"{a[1]}/{a[2]}".lstrip("/")
        return sum(n for t, n in misses.items() if t == where or t.startswith(where + "/"))
    # expansion order: headers on failing routes first, then the kinds of edit that have helped before
    prior = lambda a: (on_failing_route(a), learned.get(a[0], {}).get("mean", 0.0))
    root = Node(())
    best = ((), base_tune, [])
    original = snapshot(lib)
    for _ in range(iterations):
        node, path = root, [root]
        while len(node.seq) < DEPTH and node.children and len(node.children) >= min(4, len(acts)):
            node = max(node.children.values(), key=lambda c: c.value / c.n + UCB_C * math.sqrt(
                math.log(node.n + 1) / c.n) if c.n else float("inf"))
            path.append(node)
        if len(node.seq) < DEPTH:
            untried = [a for a in acts if a not in node.children and a not in node.seq]
            if untried:
                untried.sort(key=lambda a: (tuple(-x for x in prior(a)), rnd.random()))
                a = untried[0]
                node.children[a] = Node(node.seq + (a,))
                node = node.children[a]
                path.append(node)
        changes = [c for c in (apply(lib, writer, a) for a in node.seq) if c]
        reward = score(lib, scorer, tune) - base_tune
        restore(lib, original)
        for n in path:
            n.n += 1
            n.value += reward
        for a in node.seq:  # RL update: incremental mean reward per action kind
            st = learned.setdefault(a[0], {"n": 0, "mean": 0.0})
            st["n"] += 1
            st["mean"] += (reward - st["mean"]) / st["n"]
        if base_tune + reward > best[1] and changes:
            best = (node.seq, base_tune + reward, changes)
    save_stats(lib, learned)
    report = {"questions": {"tuning": len(tune), "held_out": len(held), "missed_targets": len(misses)},
              "iterations": iterations,
              "before": {"tuning": round(base_tune, 3), "held_out": round(base_held, 3)},
              "learned": {k: {"tries": v["n"], "mean_gain": round(v["mean"], 3)} for k, v in learned.items()},
              "kept": False, "changes": []}
    if best[0]:
        changes = [c for c in (apply(lib, writer, a) for a in best[0]) if c]
        after_held, after_tune = score(lib, scorer, held), score(lib, scorer, tune)
        report["after"] = {"tuning": round(after_tune, 3), "held_out": round(after_held, 3)}
        if after_held >= base_held and after_tune > base_tune:
            report.update(kept=True, changes=changes)
        else:
            restore(lib, original)
    return report
