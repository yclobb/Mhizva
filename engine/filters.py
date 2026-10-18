"""Apply the understanding step's filters to fact rows, and answer rank / compare (money in vs out).

Filters (one vocabulary, see intent.SCHEMA):
  period       today, yesterday, this_week, last_week, this_month, last_month, this_quarter, last_quarter,
               this_year, last_year, ytd, "2026-Q2", "2026-04"
  date_field   issued | due | paid: which of a record's dates the period applies to
  status       unpaid | paid | overdue | partly_paid | written_off
  days_overdue ">30": due more than 30 days ago and not paid
  amount       ">1000", "<50"
  category / party: words that must appear in the record
"""
import datetime as dt
import re

from . import clean
from .llm import words

PAID = re.compile(r"\b(paid|settled|received|complete|closed)\b", re.I)
NOT_PAID = re.compile(r"\b(unpaid|outstanding|open|due|pending|overdue|not paid|partially|partly)\b", re.I)


def fy_start(day):
    """Start year of the Australian financial year (1 Jul - 30 Jun) containing `day`."""
    return day.year if day.month >= 7 else day.year - 1


def window(period, today=None, date_field=None):
    """(start, end) ISO dates for a period token, or None.

    Global spec rules: Australian financial year, 1 Jul - 30 Jun. "FY2026" = 1 Jul 2025 - 30 Jun 2026.
    "BAS-Q1".."BAS-Q4" are quarters of the current financial year (Q1 = Jul-Sep); "BAS-Q1 FY2027" names one.
    For DUE dates, "this_week" / "next_week" look forward (the next 7 days / the 7 after), because that is
    what an owner means by "due this week" on any day; for other dates they are calendar weeks (Mon-Sun)."""
    today = today or dt.date.today()
    p = (period or "").strip().lower().replace(" ", "")
    if not p:
        return None
    if date_field == "due" and p in ("this_week", "next_week", "last_week"):
        a = {"this_week": today, "next_week": today + dt.timedelta(days=8),
             "last_week": today - dt.timedelta(days=14)}[p]
        return a.isoformat(), (a + dt.timedelta(days=7)).isoformat()
    m = re.fullmatch(r"fy(\d{2}|\d{4})", p)
    if m:
        y = int(m.group(1)) + (2000 if len(m.group(1)) == 2 else 0)
        return f"{y - 1}-07-01", f"{y}-06-30"
    m = re.fullmatch(r"bas-?q([1-4])(?:fy(\d{4}))?", p)
    if m:
        fy0 = int(m.group(2)) - 1 if m.group(2) else fy_start(today)
        q = int(m.group(1))
        y, mo = (fy0, 7 + 3 * (q - 1)) if q <= 2 else (fy0 + 1, 1 + 3 * (q - 3))
        end = dt.date(y + (mo + 2 > 12), (mo + 2 - 1) % 12 + 1, 1)
        end = dt.date(end.year + (end.month == 12), end.month % 12 + 1, 1) - dt.timedelta(days=1)
        return dt.date(y, mo, 1).isoformat(), end.isoformat()

    def month(y, m):
        end = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
        return dt.date(y, m, 1).isoformat(), end.isoformat()

    def quarter(y, q):
        return dt.date(y, 3 * q - 2, 1).isoformat(), month(y, 3 * q)[1]
    q_now = (today.month - 1) // 3 + 1
    week_start = today - dt.timedelta(days=today.weekday())
    simple = {
        "today": (today, today), "yesterday": (today - dt.timedelta(1), today - dt.timedelta(1)),
        "this_week": (week_start, week_start + dt.timedelta(6)),
        "last_week": (week_start - dt.timedelta(7), week_start - dt.timedelta(1)),
        "ytd": (dt.date(today.year, 1, 1), today),
    }
    if p in simple:
        a, b = simple[p]
        return a.isoformat(), b.isoformat()
    if p == "this_month":
        return month(today.year, today.month)
    if p == "last_month":
        return month(today.year - (today.month == 1), (today.month - 2) % 12 + 1)
    if p == "this_quarter":
        return quarter(today.year, q_now)
    if p == "last_quarter":
        return quarter(today.year - (q_now == 1), (q_now - 2) % 4 + 1)
    if p == "this_year":
        return f"{today.year}-01-01", f"{today.year}-12-31"
    if p == "last_year":
        return f"{today.year - 1}-01-01", f"{today.year - 1}-12-31"
    m = re.fullmatch(r"(\d{4}|this_year|last_year)-?q([1-4])", p)
    if m:
        y = {"this_year": today.year, "last_year": today.year - 1}.get(m.group(1)) or int(m.group(1))
        return quarter(y, int(m.group(2)))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", p)
    if m:
        return month(int(m.group(1)), int(m.group(2)))
    return None


def record_date(r, field):
    """The record's issued / due / paid date. Spreadsheet rows carry every date column in their fields."""
    if field in ("due", "paid"):
        for k, v in r.get("fields", {}).items():
            if re.search(field, k, re.I) and re.search(r"date|on|at", k, re.I) or k.lower() == field:
                d = clean.date(v)[0]
                if d:
                    return d
        m = re.search(rf"\b{field}\b[^.;\n]{{0,25}}?(\d{{1,2}} [A-Za-z]+ \d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})", r.get("text", ""), re.I)
        if m:
            return clean.date(m.group(1))[0]
    return r.get("date")


def status_of(r):
    """The record's own status if the document stated it (PAID stamp, part payment, quote status)."""
    return r.get("status")


def status_text(r):
    st = [v for k, v in r.get("fields", {}).items() if re.search(r"status|state|stage|paid", k, re.I)]
    return " ".join(st) if st else r.get("text", "")


def is_paid(r):
    if status_of(r):
        return status_of(r) == "paid"
    s = status_text(r)
    return bool(PAID.search(s)) and not NOT_PAID.search(s)


def outstanding_amount(r):
    """A partly paid record counts at its remaining balance; only its total line carries that balance."""
    if status_of(r) == "partly_paid" and r.get("amount") is not None:
        if not re.search(r"total", r.get("label", ""), re.I):
            return None
        return {**r, "amount": r["balance"], "label": "Balance"}
    return r


def compare_number(value, rule):
    m = re.fullmatch(r"\s*([<>]=?)\s*\$?([\d,.]+)\s*([km]?)\s*", str(rule), re.I)
    if not m or value is None:
        return True
    n = float(m.group(2).replace(",", "")) * {"k": 1e3, "m": 1e6}.get(m.group(3).lower(), 1)
    return {"<": value < n, "<=": value <= n, ">": value > n, ">=": value >= n}[m.group(1)]


def apply(rows, filters, today=None):
    """Rows that satisfy every filter. Unknown or unusable filters are ignored rather than emptying the result."""
    today = today or dt.date.today()
    f = filters or {}
    out = list(rows)
    period = f.get("period")
    field = f.get("date_field")
    if isinstance(period, list):  # compare across two periods: keep rows in either (split later by compare)
        wins = [w for w in (window(p, today, field) for p in period) if w]
        win = (min(w[0] for w in wins), max(w[1] for w in wins)) if wins else None
    else:
        win = window(period, today, field)
    if win:
        field = f.get("date_field") or "issued"
        out = [r for r in out if (d := record_date(r, field)) and win[0] <= d <= win[1]]
    status = f.get("status")
    if f.get("doc_type"):
        want = f["doc_type"].lower()
        out = [r for r in out if (r.get("kind") or "").lower() == want
               or want in ("bank", "payroll") and want in (r.get("title") or "").lower()]
    if status in ("pending", "accepted", "declined"):
        out = [r for r in out if status_of(r) == status]
    elif status == "outstanding":  # unpaid + partly paid (balance only) + overdue
        out = [outstanding_amount(r) for r in out if not is_paid(r) and status_of(r) not in ("pending", "accepted",
                                                                                             "declined")]
        out = [r for r in out if r is not None]
    elif status == "paid":
        out = [r for r in out if is_paid(r)]
    elif status == "unpaid":
        out = [outstanding_amount(r) for r in out if not is_paid(r)]
        out = [r for r in out if r is not None]
    elif status == "overdue":
        out = [r for r in out if not is_paid(r) and (re.search(r"overdue|late", status_text(r), re.I)
               or ((d := record_date(r, "due")) and d < today.isoformat()))]
    elif status in ("partly_paid", "written_off"):
        pat = {"partly_paid": r"partial|partly", "written_off": r"written.?off|write.?off|bad debt"}[status]
        out = [r for r in out if re.search(pat, status_text(r), re.I)]
    if f.get("days_overdue"):
        m = re.search(r"\d+", f["days_overdue"])
        if m:
            cutoff = (today - dt.timedelta(days=int(m.group()))).isoformat()
            out = [r for r in out if not is_paid(r) and (d := record_date(r, "due")) and d < cutoff]
    if f.get("amount"):
        out = [r for r in out if compare_number(r.get("amount"), f["amount"])]
    if f.get("currency"):  # amounts in other currencies are never mixed in
        want = f["currency"].upper()
        out = [r for r in out if r.get("amount") is None or (r.get("currency") or "AUD").upper() == want]
    if f.get("ref"):  # a named document number (INV-1043)
        ref = f["ref"].lower()
        out = [r for r in out if ref in (r.get("ref") or "").lower() or ref in r.get("text", "").lower()]
    for key in ("category", "party"):
        if f.get(key):
            want = set(words(f[key]))
            out = [r for r in out if want & set(words(f"{r['party']} {r['label']} {r['text']} "
                                                      + " ".join(r.get("fields", {}).values())))]
    return out


def top_n(question, default=5):
    m = re.search(r"\b(?:top|bottom|first|last)\s+(\d+)\b|\b(\d+)\s+(?:biggest|largest|highest|lowest|smallest)", question.lower())
    return int(m.group(1) or m.group(2)) if m else default


def rank(rows, question, group_field=None):
    """Groups ranked by total amount, largest first ('least'/'lowest'/'smallest' reverses)."""
    totals, members = {}, {}
    for r in rows:
        if r.get("amount") is None:
            continue
        key = (r.get("fields", {}).get(group_field) if group_field else None) or r["party"]
        totals[key] = totals.get(key, 0.0) + r["amount"]
        members.setdefault(key, []).append(r)
    low = bool(re.search(r"\b(least|lowest|smallest|bottom|worst-paying)\b", question.lower()))
    order = sorted(totals, key=lambda k: totals[k], reverse=not low)[:top_n(question)]
    return [(k, totals[k], members[k]) for k in order]


def compare_in_out(rows):
    """(money in, money out, rows in, rows out) over rows tagged with a direction."""
    ins = [r for r in rows if r.get("direction") == "in" and r.get("amount") is not None]
    outs = [r for r in rows if r.get("direction") == "out" and r.get("amount") is not None]
    return sum(r["amount"] for r in ins), sum(r["amount"] for r in outs), ins, outs
