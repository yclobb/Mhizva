"""Understanding step (Lucas's Tier 1): every message becomes one structured form before anything runs.

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
    "count": "how many records match",
    "list": "list or show the records that match",
    "chart": "a chart or graph of the business's numbers",
    "trend": "whether the business's numbers are rising, falling or changing over time",
}

EXAMPLES = [
    ("thanks heaps", {"intent": "chat"}),
    ("that answer was rubbish", {"intent": "chat"}),
    ("are you running on the internet?", {"intent": "about_me"}),
    ("why do leaves change colour?", {"intent": "general"}),
    ("should I take out a loan for a new van?", {"intent": "advice"}),
    ("when does the forklift lease end?", {"intent": "lookup"}),
    ("how much do customers owe us altogether", {"intent": "total", "direction": "in"}),
    ("how many quotes did we send in May", {"intent": "count"}),
    ("show all unpaid bills", {"intent": "list", "direction": "out"}),
    ("graph sales per customer", {"intent": "chart", "group_by": "customer", "direction": "in"}),
    ("is our fuel spend going up?", {"intent": "trend", "direction": "out"}),
]

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "direction": {"type": "string", "enum": ["in", "out", "none"]},
        "group_by": {"type": "string", "maxLength": 40},
        "subject": {"type": "string", "maxLength": 60},
    },
    "required": ["intent", "direction", "group_by", "subject"],
}


def rules_intent(question):
    """The keyword routing used before the parser (baseline, and fallback for the stand-in model)."""
    from .search import IDENTITY, chit_chat, general_question  # late import: search imports this module
    if IDENTITY.search(question):
        return {"intent": "about_me"}
    if chit_chat(question):
        return {"intent": "chat"}
    kind = facts.question_kind(question)
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


def prompt(question, history=(), fields=()):
    from .llm import chat
    kinds = "\n".join(f"- {k}: {v}" for k, v in INTENTS.items())
    shots = "\n".join(f"Message: {q}\nForm: {json.dumps({'intent': f['intent'], 'direction': f.get('direction', 'none'), 'group_by': f.get('group_by', ''), 'subject': ''})}"
                      for q, f in EXAMPLES)
    recent = "".join(f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['content'][:200]}\n" for h in list(history)[-2:])
    cols = f"\nColumns in the business's spreadsheets: {', '.join(sorted(fields)[:30])}" if fields else ""
    return chat(
        "You read messages sent to Mhizva, an assistant for a small business. Fill in the form for each message. "
        "direction: in = money owed to the business (its invoices, sales, customers), out = money the business "
        "pays (bills, suppliers, expenses), none = not about money. group_by: what a chart or total is split by, "
        "or empty. subject: the main thing asked about, or empty.",
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
    to the large one (Lucas: 0.5 -> 1.5). rules_weight blends in the keyword rules as a prior.
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
    return {**out, "by": "model"}


DEEP_CALLS = [0]


def cascade(fast, deep, question, history=(), fields=()):
    """Lucas's 'think more': the quick path (keyword rules + the small model's form) decides when both agree;
    when they disagree, the larger model fills the form and decides."""
    rules = rules_intent(question)
    quick = parse(fast, question, history, fields) if fast is not None else {**rules, "by": "rules"}
    keep = {k: rules[k] for k in ("direction", "group_by") if k in rules}  # the rules are reliable for these
    if quick["intent"] == rules["intent"] or deep is None:
        return {**quick, **keep, "by": "agree"}
    DEEP_CALLS[0] += 1
    slow = parse(deep, question, history, fields)
    return {**slow, **keep, "by": "deep"}
