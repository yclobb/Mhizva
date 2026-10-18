"""Cleaning messy spreadsheet values, column by column. General rules, nothing specific to one file.

  numbers    "$10M+", "$1M-$5M", "€2.5m", "4.2M", "approx. 900k", "12,000,000 USD", "155 employees", "7 open"
             -> value, plus whether it is exact, a range (low/high) or a lower bound; "n/a", "unknown",
             "Confidential" -> missing
  dates      ISO with or without time, 16/09/2018 vs 09/16/2018 (decided per column from the values that can
             only be one way), "28 Oct 17", "Sep 2021", "Q3 2021", Excel day numbers
  companies  the same company under different names ("NOTION", "The Notion", "Notion - old account") is one
             entity, keyed by its website's domain when there is one, otherwise by a cleaned name
  categories "US", "united states", "UNITED STATES" -> "United States"; "n/a", "unknown", "" -> missing
"""
import datetime as dt
import re
from collections import Counter

MISSING = {"", "n/a", "na", "none given", "unknown", "null", "nil", "-", "--", "?", "tbd", "tba", "confidential",
           "not disclosed", "needs review", "未知", "无"}
CURRENCY_MARK = [("EUR", r"€|\beur\b"), ("GBP", r"£|\bgbp\b"), ("CNY", r"¥|￥|\bcny\b|\brmb\b|元"),
                 ("AUD", r"\baud\b|a\$"), ("NZD", r"\bnzd\b|nz\$"), ("USD", r"\busd\b|us\$"), ("$", r"\$")]
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9,
         "billion": 1e9, "万": 1e4, "亿": 1e8, "千": 1e3}
NUM = r"(\d[\d,]*(?:\.\d+)?|\.\d+)\s*(k|thousand|mm|mn|m|million|bn|b|billion|万|亿|千)?(?![a-z])"


def number(text):
    """{"value", "low", "high", "kind": exact|range|at_least|approx, "currency"} or None.
    A range's value is its midpoint; "at least" uses the bound. Words around the number are ignored."""
    s = str(text).strip()
    if s.lower().strip(" .") in MISSING:
        return None
    low_s = s.lower()
    currency = next((code for code, pat in CURRENCY_MARK if re.search(pat, low_s)), None)
    nums = [(float(n.replace(",", "")) * SCALE.get((u or "").lower(), 1.0)) for n, u in re.findall(NUM, low_s, re.I)]
    if not nums:
        if re.fullmatch(r"(none|no|zero)\b.*", low_s):
            return {"value": 0.0, "low": 0.0, "high": 0.0, "kind": "exact", "currency": currency}
        return None
    neg = bool(re.match(r"^\s*(-|\()", s))
    if len(nums) >= 2 and re.search(r"\d\s*[kmb万]?\s*(-|–|to|~)\s*[$€£¥]?\s*\d", low_s):
        lo, hi = nums[0], nums[1]
        if hi < lo and re.search(r"[kmb]\b", low_s):  # "$1-5M": the unit applies to both ends
            lo = lo * (hi / nums[1]) if nums[1] else lo
        return {"value": (lo + hi) / 2, "low": lo, "high": hi, "kind": "range", "currency": currency}
    v = -nums[0] if neg else nums[0]
    if re.search(r"\+\s*$|\bover\b|\bmore than\b|\bat least\b|以上", low_s):
        return {"value": v, "low": v, "high": None, "kind": "at_least", "currency": currency}
    kind = "approx" if re.search(r"approx|about|around|~|circa|\bca\.|约|大约", low_s) else "exact"
    return {"value": v, "low": v, "high": v, "kind": kind, "currency": currency}


UNIT_WORDS = r"(employees?|staff|people|users?|seats?|units?|items?|pcs|open|deals?|points?|pts|stars?|days?|" \
             r"hours?|hrs?|kg|g|km|m2|sqm|percent|usd|aud|eur|gbp|cny|nzd|rmb|approx|about|around|circa|to|" \
             r"over|more|than|at|least|thousand|million|billion|k|m|mm|mn|b|bn|ca|个|人|万|亿|千|元)"


def looks_numeric(text):
    """True when a cell is mainly a number: "155 employees", "$1M-$5M", "approx. 900k" yes;
    "1 Hill St, Brisbane" or "INV-2001" no."""
    if number(text) is None:
        return False
    rest = re.sub(NUM, " ", str(text).lower())
    rest = re.sub(UNIT_WORDS + r"\b", " ", rest)
    rest = re.sub(r"[\s$€£¥￥+\-–~().,/%:]+", " ", rest).strip()
    return len(rest) <= 2


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                         "nov", "dec"])}


def date_convention(values):
    """'dmy' or 'mdy' for a column of a/b/yyyy dates, from the values that can only be read one way."""
    d = m = 0
    for v in values:
        x = re.match(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", str(v))
        if x:
            a, b = int(x.group(1)), int(x.group(2))
            d += a > 12 >= b
            m += b > 12 >= a
    return "mdy" if m > d else "dmy"  # Australian default when nothing decides it


def year4(y):
    y = int(y)
    return y + (2000 if y < 70 else 1900) if y < 100 else y


def date(text, convention="dmy"):
    """(iso date, precision) or (None, None). Precision: day | month | quarter."""
    s = str(text).strip()
    if s.lower() in MISSING:
        return None, None
    try:
        x = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if x:
            return dt.date(int(x.group(1)), int(x.group(2)), int(x.group(3))).isoformat(), "day"
        x = re.match(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*[日号])?", s)
        if x:
            return dt.date(int(x.group(1)), int(x.group(2)), int(x.group(3) or 1)).isoformat(), \
                "day" if x.group(3) else "month"
        x = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$", s)
        if x:
            a, b, y = int(x.group(1)), int(x.group(2)), year4(x.group(3))
            day, mon = (a, b) if convention == "dmy" else (b, a)
            if mon > 12:
                day, mon = mon, day
            return dt.date(y, mon, day).isoformat(), "day"
        x = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{2,4})$", s, re.I)
        if x and x.group(2).lower() in MONTHS:
            return dt.date(year4(x.group(3)), MONTHS[x.group(2).lower()], int(x.group(1))).isoformat(), "day"
        x = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})$", s, re.I)
        if x and x.group(1).lower() in MONTHS:
            return dt.date(int(x.group(3)), MONTHS[x.group(1).lower()], int(x.group(2))).isoformat(), "day"
        x = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{4})$", s, re.I)
        if x and x.group(1).lower() in MONTHS:
            return dt.date(int(x.group(2)), MONTHS[x.group(1).lower()], 1).isoformat(), "month"
        x = re.match(r"q([1-4])\s*[-/ ]?\s*(\d{4})$|(\d{4})\s*[-/ ]?\s*q([1-4])$", s, re.I)
        if x:
            q, y = (x.group(1), x.group(2)) if x.group(1) else (x.group(4), x.group(3))
            return dt.date(int(y), 3 * int(q) - 2, 1).isoformat(), "quarter"
        n = float(s)
        if 20000 < n < 80000:  # Excel serial day number
            return (dt.date(1899, 12, 30) + dt.timedelta(days=int(n))).isoformat(), "day"
    except ValueError:
        pass
    return None, None


SUFFIXES = r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|pty|llc|gmbh|plc|ag|sa|srl|bv|ab|oy|as|holdings)\b\.?"


def domain(url):
    s = str(url).strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)
    s = re.sub(r"^www\d?\.", "", s)
    s = re.split(r"[/?#\s]", s)[0]
    return s if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", s) else None


def name_key(name):
    s = str(name).lower().strip()
    s = re.split(r"\s+[-–(]\s*(old|duplicate|dup|test|inactive|former|legacy|copy)\b", s)[0]
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(SUFFIXES, "", s)
    s = re.sub(r"[^\w一-鿿]+", " ", s).strip()
    return s or None


def entities(names, websites=None):
    """Canonical display name per row. Rows sharing a website domain, or failing that a cleaned name, are one
    entity; the display name is the cleanest common spelling seen for it."""
    websites = websites or [None] * len(names)
    keys = []
    by_name = {}
    for n, w in zip(names, websites):
        k = name_key(n)
        d = domain(w) if w else None
        if d and k:
            by_name.setdefault(k, Counter())[d] += 1
        keys.append((k, d))
    # a name seen with a domain belongs to that domain's entity even on rows without a website
    name_dom = {k: c.most_common(1)[0][0] for k, c in by_name.items()}
    groups = [d or name_dom.get(k) or k for k, d in keys]
    spellings = {}
    for g, n in zip(groups, names):
        if g:
            spellings.setdefault(g, Counter())[pretty(n)] += 1
    display = {g: c.most_common(1)[0][0] for g, c in spellings.items()}
    return [display.get(g) if g else None for g in groups]


def pretty(name):
    k = name_key(name) or str(name).strip()
    raw = re.split(r"\s+[-–(]\s*(old|duplicate|dup|test|inactive|former|legacy|copy)\b", str(name).strip(), flags=re.I)[0]
    raw = re.sub(r"^the\s+", "", raw, flags=re.I).strip()
    if raw.isupper() or raw.islower():  # "NOTION", "stripe" -> "Notion", "Stripe"
        return " ".join(w.capitalize() for w in k.split())
    return raw.strip(" .,") or raw  # matching ignores suffixes; the displayed name keeps them ("Timber Co")


COUNTRIES = {"united states": ["us", "usa", "u.s.", "u.s.a.", "united states of america", "america", "美国"],
             "united kingdom": ["uk", "gb", "great britain", "britain", "england", "英国"],
             "australia": ["au", "aus", "澳大利亚", "澳洲"], "canada": ["ca", "can", "加拿大"],
             "new zealand": ["nz", "新西兰"], "germany": ["de", "deutschland", "德国"], "france": ["fr", "法国"],
             "china": ["cn", "prc", "中国"], "india": ["in", "印度"], "japan": ["jp", "日本"],
             "singapore": ["sg", "新加坡"], "ireland": ["ie"], "netherlands": ["nl", "holland"]}
ALIAS = {a: c for c, al in COUNTRIES.items() for a in al + [c]}


def category(value, column=""):
    """Tidy a category value: missing markers -> None, countries unified, case and spacing normalised."""
    s = re.sub(r"\s+", " ", str(value)).strip()
    if s.lower().strip(" .") in MISSING:
        return None
    if re.search(r"country|nation|国家", column, re.I) or s.lower() in ALIAS and len(s) > 2:
        c = ALIAS.get(s.lower().strip(" ."))
        if c:
            return c.title()
    return s[0].upper() + s[1:] if s.islower() or s.isupper() else s
