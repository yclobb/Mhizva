"""Check and normalise worksheet batches into one clean training file.

Run: python -I -X utf8 training/check.py            (reads training/*batch*.jsonl and seed_*.jsonl)
Writes training/clean.jsonl and prints what was fixed, rejected, duplicated, and the mix of intents.

Normalising (generator formats differ; spec v2.1 is the target):
  null / empty filter values dropped; filters.doc_id -> filters.ref; filters.tax -> form.money_field="tax";
  missing agg / money_field left out (defaults: sum / total).
Rejected: invalid JSON, unknown intent/direction/filter key/status/period, empty message.
Flagged (kept, listed for review): rule breaks that are probably label mistakes.
"""
import glob
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from engine.intent import INTENTS, PERIODS, STATUSES  # noqa: E402

DIRECTIONS = {"in", "out", "net", "none"}
FILTER_KEYS = {"period", "date_field", "status", "days_overdue", "amount", "category", "party", "ref"}
PERIOD_RE = re.compile(r"(" + "|".join(p for p in PERIODS if p) + r"|\d{4}-q[1-4]|\d{4}-\d{2}|this_year-q[1-4])", re.I)
MONEY_INTENTS = {"total", "count", "list", "rank", "compare", "chart", "trend"}


def normalise(o):
    if "form" not in o:  # flat rows (seed file): intent/direction/... at the top level
        o = {"source": o.get("source"), "lang": o.get("lang", "en"), "q": o["q"], "history": o.get("history", []),
             "form": {k: o[k] for k in ("intent", "direction", "group_by", "filters", "subject", "agg", "money_field")
                      if k in o}}
    f = o["form"]
    filters = {k: v for k, v in (f.get("filters") or {}).items() if v not in (None, "", [], {})}
    if "doc_id" in filters:
        filters["ref"] = filters.pop("doc_id")
    tax = filters.pop("tax", None)
    if tax:
        f["money_field"] = "tax"
    f["filters"] = filters
    for k in ("group_by", "subject"):
        f[k] = f.get(k) or ""
    return o


def problems(o):
    """(reject reasons, review flags)."""
    bad, flag = [], []
    f = o.get("form", {})
    if not str(o.get("q", "")).strip():
        bad.append("empty message")
    if f.get("intent") not in INTENTS:
        bad.append(f"unknown intent {f.get('intent')}")
    if f.get("direction") not in DIRECTIONS:
        bad.append(f"unknown direction {f.get('direction')}")
    for k, v in f.get("filters", {}).items():
        if k not in FILTER_KEYS:
            bad.append(f"unknown filter {k}")
        elif k == "status" and v not in STATUSES:
            bad.append(f"unknown status {v}")
        elif k == "period":
            for p in (v if isinstance(v, list) else [v]):
                if not PERIOD_RE.fullmatch(str(p)):
                    bad.append(f"unknown period {p}")
        elif k == "date_field" and v not in ("issued", "due", "paid"):
            bad.append(f"unknown date_field {v}")
    if f.get("agg", "sum") not in ("sum", "avg", "min", "max"):
        bad.append(f"unknown agg {f.get('agg')}")
    if f.get("money_field", "total") not in ("total", "subtotal", "tax"):
        bad.append(f"unknown money_field {f.get('money_field')}")
    it, d = f.get("intent"), f.get("direction")
    if it in ("chat", "about_me", "general", "advice") and d != "none":
        flag.append(f"{it} should have direction none")
    if it in MONEY_INTENTS - {"count"} and d == "none" and it not in ("trend",):
        flag.append(f"{it} about money but direction none")
    if re.search(r"breakdown|per (customer|client|supplier|month)|each (customer|client|supplier)", o.get("q", ""), re.I) \
            and it == "list":
        flag.append("breakdown should be total + group_by")
    if f.get("group_by") in ("day", "week", "date") and f.get("filters", {}).get("period") and it != "chart":
        flag.append("time used as group_by and period")
    return bad, flag


def main():
    files = sorted(glob.glob(os.path.join(HERE, "seed_*.jsonl")) + glob.glob(os.path.join(HERE, "*batch*.jsonl")))
    seen, clean, rejected, flagged, dupes = set(), [], [], [], 0
    for path in files:
        for n, line in enumerate(open(path, encoding="utf-8"), 1):
            line = line.strip()
            if not line:
                continue
            try:
                o = normalise(json.loads(line))
            except (ValueError, KeyError, TypeError) as e:
                rejected.append((os.path.basename(path), n, f"invalid: {e}"))
                continue
            bad, flag = problems(o)
            if bad:
                rejected.append((os.path.basename(path), n, "; ".join(bad)))
                continue
            key = (re.sub(r"\W+", " ", o["q"].lower()).strip(), json.dumps(o.get("history", []), sort_keys=True))
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            if flag:
                flagged.append((os.path.basename(path), n, o["q"], "; ".join(flag)))
            clean.append(o)
    with open(os.path.join(HERE, "clean.jsonl"), "w", encoding="utf-8") as fh:
        for o in clean:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"{len(files)} files -> {len(clean)} clean rows ({dupes} duplicates removed, {len(rejected)} rejected, "
          f"{len(flagged)} flagged for review)")
    print("intents:", dict(Counter(o["form"]["intent"] for o in clean).most_common()))
    print("languages:", dict(Counter(o.get("lang") for o in clean)), "| sources:", dict(Counter(o.get("source") for o in clean)))
    print("with history:", sum(1 for o in clean if o.get("history")))
    for r in rejected[:30]:
        print("REJECT", *r)
    for r in flagged[:30]:
        print("REVIEW", *r)


if __name__ == "__main__":
    main()
