"""Routing diagnostics: log every routing step, rank folders by (entropy x error), and suggest header keywords
from failed queries. This supersedes the MCTS organiser as the way headers get fixed: real failures are the
supervision, synthetic questions are not (they are generated from the documents, so headers already fit them).

Log: <library>/.mhizva/routing.jsonl, one line per question:
  {"q", "steps": [{"index", "entropy", "margin", "chosen"}], "read": [...], "found": bool, "target": path|null}
"found" is the system's own outcome (an answer grounded in the documents). "target" is filled when the right
document is known (an evaluation set, or later user feedback such as clicking "wrong answer").
Logs stay on this computer.

Reading the diagnostics (per routing step):
  high entropy + wrong   the header does not distinguish its children   -> add distinguishing keywords
  low entropy + wrong    the header is confidently misleading           -> rewrite it (keywords won't help)
  high entropy + right   probably luck                                  -> counted as an error
"""
import json
import math
import os
import time
from collections import defaultdict

from .llm import words

HIGH_ENTROPY = 0.6  # as a fraction of the maximum entropy log(n) for that step


def log_path(lib):
    d = os.path.join(lib.root, ".mhizva")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "routing.jsonl")


def record(lib, question, result, target=None):
    steps = [{"index": s["index"], "entropy": s.get("entropy"), "margin": s.get("margin"),
              "n": len(s.get("scores", {})), "chosen": s.get("chosen", [])} for s in result.get("trace", [])]
    if not steps:
        return
    line = {"t": round(time.time()), "q": question, "steps": steps, "found": bool(result.get("found")),
            "read": [q["path"] for q in result.get("quotes", [])], "target": target}
    with open(log_path(lib), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def load(lib):
    try:
        return [json.loads(l) for l in open(log_path(lib), encoding="utf-8") if l.strip()]
    except OSError:
        return []


def wrong(entry, step):
    """Was this routing step wrong? With a known target: it did not choose the branch leading to it.
    Without one: the question ended unanswered. A high-entropy step counts as wrong even when right."""
    if entry.get("target"):
        prefix = step["index"] if step["index"] != "Library" else ""
        on_path = entry["target"].startswith(prefix)
        if not on_path:
            return None  # this step was off the target's path entirely (a backtrack); not this folder's fault
        child = entry["target"][len(prefix):].lstrip("/").split("/")[0]
        miss = child not in step["chosen"]
    else:
        miss = not entry["found"]
    unsure = step["n"] > 1 and step["entropy"] is not None and step["entropy"] >= HIGH_ENTROPY * math.log(step["n"])
    return miss or unsure


def rank_folders(lib, top=0.1):
    """Folders ordered by entropy x error rate, with the kind of fix each needs. Returns the top share."""
    stats = defaultdict(lambda: {"n": 0, "err": 0, "ent": 0.0, "confident_wrong": 0})
    for e in load(lib):
        for st in e["steps"]:
            w = wrong(e, st)
            if w is None:
                continue
            s = stats[st["index"]]
            s["n"] += 1
            s["err"] += w
            s["ent"] += st["entropy"] or 0.0
            maxh = math.log(st["n"]) if st["n"] > 1 else 1.0
            if w and (st["entropy"] or 0) < 0.3 * maxh:
                s["confident_wrong"] += 1
    rows = []
    for folder, s in stats.items():
        err, ent = s["err"] / s["n"], s["ent"] / s["n"]
        fix = "rewrite header (confidently misleading)" if s["confident_wrong"] > s["err"] / 2 \
            else "add distinguishing keywords (vague)"
        rows.append({"folder": folder, "questions": s["n"], "error_rate": round(err, 2),
                     "mean_entropy": round(ent, 3), "priority": round(ent * err, 3), "fix": fix if s["err"] else "none"})
    rows.sort(key=lambda r: -r["priority"])
    return rows[:max(1, math.ceil(len(rows) * top))] if rows else []


def keyword_gaps(lib, folder):
    """For failed questions routed through `folder`: words of the question that appear in none of its children's
    headers. Those are the candidate keywords to add (supervised by real failures, no search needed)."""
    entries = lib.entries("" if folder == "Library" else folder)
    have = set()
    for c in entries:
        have |= set(words(f"{c.name} {c.summary} {' '.join(c.keywords)}"))
    gaps = defaultdict(int)
    for e in load(lib):
        if any(st["index"] == folder and wrong(e, st) for st in e["steps"]):
            for w in set(words(e["q"])) - have:
                if len(w) > 2:
                    gaps[w] += 1
    return sorted(gaps.items(), key=lambda kv: -kv[1])
