"""Understanding step (the creator's Tier-1 design): every message becomes one structured form before anything runs.

The model fills a fixed JSON form with schema-constrained decoding, so it can only output valid fields:
    {"intent": ..., "direction": "in"|"out"|"none", "group_by": str, "subject": str}
Code then checks the form against the real data and does the work; the writer only phrases results.
rules_intent() is the older keyword routing, kept as the baseline and as the fallback when no model runs.
"""
import json
import re

from . import facts

INTENTS = {
    "chat": "small talk, greetings, thanks, jokes, insults, opinions or remarks about the conversation",
    "about_me": "questions about the assistant itself: what it is, which model, who made it, what it can do, "
                "its limits",
    "general": "questions about the world or general knowledge, not about this business's own records",
    "advice": "asking what to do about a personal or business decision (money, legal, hiring, investing)",
    "lookup": "one fact from the business's own documents (a date, a name, a price, a term)",
    "total": "add up or average money across the business's records",
    "rank": "the top or bottom records or groups: who owes the most, best customers, biggest expenses",
    "compare": "compare two amounts or groups: money in vs money out, this month vs last month, A vs B",
    "count": "how many records match",
    "list": "list or show the records that match",
    "chart": "a chart or graph of the business's numbers",
    "trend": "whether the business's numbers are rising, falling or changing over time",
    "stats": "statistics or analysis of a series of numbers: averages, volatility, returns, drawdown, moving "
             "averages, seasonality (sales, costs, stock prices)",
}

EXAMPLES = [
    ("thanks heaps", {"intent": "chat"}),
    ("that answer was rubbish", {"intent": "chat"}),
    ("are you running on the internet?", {"intent": "about_me"}),
    ("why do leaves change colour?", {"intent": "general"}),
    ("should I take out a loan for a new van?", {"intent": "advice"}),
    ("when does the forklift lease end?", {"intent": "lookup"}),
    ("how much do customers owe us altogether", {"intent": "total", "direction": "in",
                                                 "filters": {"status": "unpaid"}}),
    ("who owes us the most", {"intent": "rank", "direction": "in", "group_by": "customer",
                              "filters": {"status": "unpaid"}}),
    ("did we make more than we spent last month", {"intent": "compare", "direction": "net",
                                                    "filters": {"period": "last_month"}}),
    ("bills due this week over $500", {"intent": "list", "direction": "out",
                                       "filters": {"period": "this_week", "date_field": "due", "amount": ">500"}}),
    ("how many quotes did we send in May", {"intent": "count", "direction": "in", "filters": {"period": "2026-05"}}),
    ("show all unpaid bills", {"intent": "list", "direction": "out", "filters": {"status": "unpaid"}}),
    ("graph sales per customer", {"intent": "chart", "group_by": "customer", "direction": "in"}),
    ("is our fuel spend going up?", {"intent": "trend", "direction": "out"}),
]

PERIODS = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter",
           "last_quarter", "this_year", "last_year", "ytd", ""]
STATUSES = ["unpaid", "paid", "overdue", "partly_paid", "written_off", ""]
# The form (the creator's schema, 2026-10-18): what is asked, which way the money goes, how it is grouped,
# and filters in one consistent vocabulary. period may also be "2026-Q2" or "2026-04".
SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "direction": {"type": "string", "enum": ["in", "out", "net", "none"]},
        "group_by": {"type": "string", "maxLength": 40},
        "filters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "maxLength": 12},
                "date_field": {"type": "string", "enum": ["issued", "due", "paid", ""]},
                "status": {"type": "string", "enum": STATUSES},
                "days_overdue": {"type": "string", "maxLength": 8},
                "amount": {"type": "string", "maxLength": 16},
                "category": {"type": "string", "maxLength": 40},
                "party": {"type": "string", "maxLength": 60},
            },
        },
        "subject": {"type": "string", "maxLength": 60},
    },
    "required": ["intent", "direction", "group_by", "filters", "subject"],
}


def rules_intent(question):
    """The keyword routing used before the parser (baseline, and fallback for the stand-in model)."""
    from .search import IDENTITY, chit_chat, general_question  # late import: search imports this module
    if IDENTITY.search(question):
        return {"intent": "about_me"}
    if chit_chat(question):
        return {"intent": "chat"}
    kind = facts.question_kind(question)
    q = question.lower()
    if re.search(r"\b(compare|versus|vs\.?|net profit|profit|cash ?flow|more or less|spend more|make more)\b", q):
        kind = "compare"
    elif kind in ("aggregate", "lookup", "table") and re.search(
            r"\b(top|most|highest|largest|biggest|least|lowest|smallest|best|worst)\b", q):
        kind = "rank"
    if kind == "aggregate":
        kind = "count" if re.search(r"\bhow many\b|几个|多少个|多少份", question.lower()) else "total"
    if kind == "table":
        kind = "list"
    if kind == "lookup" and general_question(question):
        kind = "general"
    out = {"intent": kind}
    way = facts.money_direction(question)
    if way:
        out["direction"] = way
    g = re.search(r"\b(?:by|per|for each)\s+([a-z]+)", question.lower())
    if g:
        out["group_by"] = g.group(1)
    return out


def form_of(f):
    """A complete form with defaults, in the schema's field order."""
    return {"intent": f["intent"], "direction": f.get("direction", "none"), "group_by": f.get("group_by", ""),
            "filters": f.get("filters", {}), "subject": f.get("subject", "")}


def prompt(question, history=(), fields=()):
    from .llm import chat
    kinds = "\n".join(f"- {k}: {v}" for k, v in INTENTS.items())
    shots = "\n".join(f"Message: {q}\nForm: {json.dumps(form_of(f))}" for q, f in EXAMPLES)
    recent = "".join(f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['content'][:200]}\n" for h in list(history)[-2:])
    cols = f"\nColumns in the business's spreadsheets: {', '.join(sorted(fields)[:30])}" if fields else ""
    return chat(
        "You read messages sent to Mhizva, an assistant for a small business. Fill in the form for each message. "
        "direction: in = money owed to the business (its invoices, sales, customers), out = money the business "
        "pays (bills, suppliers, expenses), net = both together (profit, cash flow, balance), none = not about "
        "money. group_by: what a total, ranking or chart is split by, or empty. filters: only what the message "
        "limits it to: period (" + ", ".join(p for p in PERIODS if p) + ", or 2026-Q2 / 2026-04), date_field "
        "(issued, due or paid), status (" + ", ".join(x for x in STATUSES if x) + "), days_overdue (e.g. >30), "
        "amount (e.g. >1000), category, party. subject: the main thing asked about, or empty.",
        f"Intents:\n{kinds}{cols}\n\nExamples:\n{shots}\n\n"
        + (f"Recent conversation:\n{recent}\n" if recent else "")
        + f"Message: {question}\nForm:")


LETTERS = "ABCDEFGHIJ"
SHOTS = [("thanks heaps", "chat"), ("that answer was rubbish", "chat"), ("when do you make things up?", "about_me"),
         ("are you running on the internet?", "about_me"), ("why do leaves change colour?", "general"),
         ("what does depreciation mean?", "general"), ("should I take out a loan for a new van?", "advice"),
         ("when does the forklift lease end?", "lookup"), ("who supplies our timber?", "lookup"),
         ("how much do customers owe us altogether", "total"), ("how many quotes did we send in May", "count"),
         ("show all unpaid bills", "list"), ("graph sales per customer", "chart"),
         ("is our fuel spend going up?", "trend")]


def pick_prompt(question, history=()):
    from .llm import chat
    names = list(INTENTS)
    options = "\n".join(f"{LETTERS[i]}) {k}: {INTENTS[k]}" for i, k in enumerate(names))
    shots = "\n".join(f"Message: {q}\nAnswer: {LETTERS[names.index(k)]}" for q, k in SHOTS)
    recent = "".join(f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['content'][:200]}\n" for h in list(history)[-2:])
    return chat("You sort messages sent to Mhizva, an assistant for a small business's documents.",
                f"Kinds of message:\n{options}\n\nExamples:\n{shots}\n\n"
                + (f"Recent conversation:\n{recent}\n" if recent else "")
                + f"Message: {question}\nWhich kind is it? Answer with one letter.\nAnswer:")


def understand(llm, question, history=(), rules_weight=0.0):
    """Intent by one-letter pick (no free text). With a TeamLLM the small model decides and hands close calls
    to the large one (the creator's design: 0.5 -> 1.5). rules_weight blends in the keyword rules as a prior.
    Direction and group_by come from the rules (they are reliable for those)."""
    rules = rules_intent(question)
    if not hasattr(llm, "form"):  # stand-in model
        return {**rules, "by": "rules", "confidence": 1.0}
    names = list(INTENTS)
    probs = llm.pick(pick_prompt(question, history), list(LETTERS[:len(names)]))
    scores = {k: probs[LETTERS[i]] for i, k in enumerate(names)}
    if rules_weight:
        scores = {k: (1 - rules_weight) * v + (rules_weight if k == rules["intent"] else 0) for k, v in scores.items()}
    ranked = sorted(scores, key=scores.get, reverse=True)
    out = {k: v for k, v in rules.items() if k != "intent"}
    return {**out, "intent": ranked[0], "confidence": round(scores[ranked[0]], 3),
            "runner_up": ranked[1], "margin": round(scores[ranked[0]] - scores[ranked[1]], 3), "by": "model"}


def parse(llm, question, history=(), fields=()):
    """The structured form for a message. Falls back to the keyword rules if the model can't fill it."""
    if not hasattr(llm, "form"):
        return {**rules_intent(question), "by": "rules"}
    try:
        out = llm.form(prompt(question, history, fields), SCHEMA)
    except Exception:
        out = None
    if not out or out.get("intent") not in INTENTS:
        return {**rules_intent(question), "by": "rules"}
    if out.get("direction") == "none":
        out.pop("direction")
    if not out.get("group_by"):
        out.pop("group_by", None)
    out["filters"] = {k: v for k, v in (out.get("filters") or {}).items() if v}
    return {**out, "by": "model"}


DEEP_CALLS = [0]


def cascade(fast, deep, question, history=(), fields=(), always=False):
    """the creator's 'think more' design: the quick path (keyword rules + the small model's form) decides when both agree;
    when they disagree, the larger model fills the form and decides."""
    rules = rules_intent(question)
    quick = parse(fast, question, history, fields) if fast is not None else {**rules, "by": "rules"}
    keep = {k: rules[k] for k in ("direction", "group_by") if k in rules}  # the rules are reliable for these
    if deep is None or (quick["intent"] == rules["intent"] and not always):  # high effort: always think
        return {**quick, **keep, "by": "agree"}
    DEEP_CALLS[0] += 1
    slow = parse(deep, question, history, fields)
    return {**slow, **keep, "by": "deep"}
