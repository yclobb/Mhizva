"""Spreadsheets (CSV, TSV, XLSX): every row becomes a record, columns are recognised by their names.

No model is needed per row. Columns are given roles from their headers:
  money  - Total / Amount / Subtotal / Tax / Price / Cost / Balance ... (currency from the header: USD, AUD, ¥ ...)
  date   - anything with "date" (Invoice_Date first, then other dates such as Due_Date)
  party  - Customer / Client / Debtor / Company / Vendor / Supplier / Creditor / Payee / Name
  ref    - Invoice_Number / Ref / ID / No.
Every other column (status, description, address) is kept as text on the record, so questions can match it.
"""
import csv
import datetime as dt
import io
import re
import zipfile
from collections import Counter

from . import clean
from .facts import REF

TABULAR = (".csv", ".tsv", ".xlsx")
ROWS_PER_SECTION = 25

MONEY_COL = re.compile(r"total|amount|subtotal|tax|gst|vat|price|cost|balance|fee|paid|due_usd|due_aud|revenue|"
                       r"sales|income|turnover|budget|arr|mrr|"
                       r"金额|合计|税|价格|费用|余额", re.I)
NOT_MONEY = re.compile(r"date|number|no\b|id\b|qty|quantity|rate|percent|%|count|days", re.I)
DATE_COL = re.compile(r"date|日期", re.I)
PARTY_COLS = ["customer", "client", "debtor", "company", "vendor", "supplier", "creditor", "payee", "payer",
              "name", "客户", "供应商", "公司", "名称"]
ID_COL = re.compile(r"^(index|row|#|no\.?)$|\bid\b|row.?id|number|year|postcode|zip|phone", re.I)
REF_COL = re.compile(r"invoice.?(number|no|#|id)|^ref|reference|^id$|number|编号|单号", re.I)
CURRENCY = [("USD", r"usd|us\$"), ("AUD", r"aud|a\$"), ("CNY", r"cny|rmb|¥|元|人民币"), ("EUR", r"eur|€"),
            ("GBP", r"gbp|£"), ("NZD", r"nzd")]


def read_table(filename, data):
    """(header, rows) from a CSV/TSV/XLSX file."""
    name = filename.lower()
    if name.endswith(".xlsx"):
        return read_xlsx(data)
    text = data.decode("utf-8-sig", "replace")
    dialect = csv.excel_tab if name.endswith(".tsv") else csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return [h.strip() for h in rows[0]], rows[1:]


def read_xlsx(data):
    """First sheet of an .xlsx, standard library only (shared strings + inline strings + numbers)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            for si in re.findall(r"<si>(.*?)</si>", xml, re.S):
                shared.append(unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))
        sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n))[0]
        xml = z.read(sheet).decode("utf-8", "replace")
    rows = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
        cells = {}
        for ref, attrs, body in re.findall(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', row, re.S):
            v = re.search(r"<v>(.*?)</v>", body, re.S)
            t = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
            if 't="s"' in attrs and v:
                val = shared[int(v.group(1))]
            elif t:
                val = unescape(t.group(1))
            else:
                val = v.group(1) if v else ""
            cells[col_index(ref)] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    if not rows:
        return [], []
    return [h.strip() for h in rows[0]], rows[1:]


def unescape(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def nice(h):
    """'Total_Due_USD' -> 'Total Due USD'."""
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", h)).strip()


def roles(header, rows):
    """Column roles from header names, checked against the values (a money column must hold numbers)."""
    sample = rows[:200]

    def numeric(i):
        """Mostly numbers once messy text is understood ("$1M-$5M", "155 employees"), ignoring blanks/n/a."""
        vals = [r[i] for r in sample if i < len(r) and r[i].strip().lower() not in clean.MISSING]
        return bool(vals) and sum(1 for v in vals if clean.looks_numeric(v)) >= 0.6 * len(vals)
    money = [i for i, h in enumerate(header) if MONEY_COL.search(h) and not NOT_MONEY.search(h) and numeric(i)]
    dates = sorted([i for i, h in enumerate(header) if DATE_COL.search(h)],
                   key=lambda i: (0 if re.search(r"invoice|issue|开票", header[i], re.I) else 1, i))
    website = next((i for i, h in enumerate(header) if re.search(r"web|url|domain|site", h, re.I)), None)
    party = None
    for word in PARTY_COLS:
        party = next((i for i, h in enumerate(header) if word in h.lower() and i not in money), None)
        if party is not None:
            break
    ref = next((i for i, h in enumerate(header) if REF_COL.search(h) and i not in money), None)
    # other numeric columns (Close, Volume, Units, Score) are measures: analysable, but not money
    measures = [i for i, h in enumerate(header) if i not in money and i not in dates and i != ref and numeric(i)
                and not ID_COL.search(h)]
    return {"money": money, "dates": dates, "party": party, "ref": ref, "measures": measures, "website": website}


def to_number(v):
    x = clean.number(v)
    if x is not None:
        return x["value"]
    return to_number_plain(v)


def to_number_plain(v):
    s = re.sub(r"[,\s$¥￥€£]|(?i:usd|aud|cny|rmb)", "", str(v))
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def to_date(v, convention="dmy"):
    v = str(v).strip()
    found = clean.date(v, convention)[0]
    if found:
        return found
    try:  # Excel serial day number
        n = float(v)
        if 20000 < n < 80000:
            return (dt.date(1899, 12, 30) + dt.timedelta(days=int(n))).isoformat()
    except ValueError:
        pass
    return None


def currency_of(header_text, default="AUD"):
    for code, pat in CURRENCY:
        if re.search(pat, header_text, re.I):
            return code
    return default


def record_text(header, row):
    return "; ".join(f"{nice(h)}: {row[i].strip()}" for i, h in enumerate(header) if i < len(row) and row[i].strip())


def markdown_sections(title, header, rows):
    """Readable text for search: each record as one 'Column: value; ...' paragraph, 25 records a section."""
    out = []
    for j in range(0, len(rows), ROWS_PER_SECTION):
        part = rows[j:j + ROWS_PER_SECTION]
        body = "\n\n".join(record_text(header, r) for r in part)
        out.append(f"# {title}, rows {j + 1}-{j + len(part)}\n\n{body}")
    return out


def dollar_currency(values, header_cur):
    """What a bare "$" means in this column: the column's explicit dollar currency if it has one
    ("12,000,000 USD" elsewhere in it), else the header's, else the business's default."""
    seen = Counter(x["currency"] for x in (clean.number(v) for v in values) if x and x["currency"] not in (None, "$"))
    for code, _ in seen.most_common():
        if code in ("USD", "AUD", "NZD"):
            return code
    return header_cur


def facts_from_rows(path_of_row, title, header, rows, cls):
    """One fact per money or measure cell, after cleaning. record = file + row number, so totals count each
    row once. Ranges, lower bounds and estimates keep their kind so answers can say "about" / "at least"."""
    rl = roles(header, rows)
    default_cur = currency_of(" ".join(header))
    col = lambda i: [r[i] if i < len(r) else "" for r in rows]
    conventions = {i: clean.date_convention(col(i)) for i in rl["dates"]}
    dollar = {i: dollar_currency(col(i), currency_of(header[i], default_cur)) for i in rl["money"]}
    names = col(rl["party"]) if rl["party"] is not None else None
    canon = clean.entities(names, col(rl["website"]) if rl["website"] is not None else None) if names else None
    special = set(rl["money"] + rl["measures"] + rl["dates"]) | {rl["ref"], rl["party"], rl["website"]}
    small = {i for i in range(len(header)) if i not in special
             and len({(r[i] if i < len(r) else "").strip().lower() for r in rows}) <= 50}
    out = []
    for n, row in enumerate(rows, 1):
        cell = lambda i: row[i].strip() if i is not None and i < len(row) else ""
        dates = [d for d in (to_date(cell(i), conventions[i]) for i in rl["dates"]) if d]
        party = (canon[n - 1] if canon else None) or cell(rl["party"]) or cls["party"]
        text = record_text(header, row)
        m = REF.search(text)
        ref = cell(rl["ref"]) if rl["ref"] is not None else (m.group(0) if m else None)
        fields = {}
        for i, h in enumerate(header):
            v = cell(i)
            if not v:
                continue
            fields[nice(h)] = (clean.category(v, h) or "") if i in small else v
        if rl["party"] is not None:
            fields[nice(header[rl["party"]])] = party
        fields = {k: v for k, v in fields.items() if v}
        for i in rl["money"] + rl["measures"]:
            x = clean.number(cell(i))
            if x is None:
                continue
            cur = ""
            if i in rl["money"]:
                cur = x["currency"] if x["currency"] not in (None, "$") else dollar[i]
            out.append({"doc": path_of_row(n), "record": f"{path_of_row(n)}#{n}", "row": n, "title": title,
                        "party": party, "kind": cls["kind"], "label": nice(header[i]), "amount": x["value"],
                        "value_kind": x["kind"], "low": x["low"], "high": x["high"], "currency": cur,
                        "date": dates[0] if dates else None, "date_from": "row" if dates else None,
                        "text": text[:1000], "ref": ref, "direction": cls.get("direction"), "fields": fields})
        if not rl["money"] and not rl["measures"] and dates:
            out.append({"doc": path_of_row(n), "record": f"{path_of_row(n)}#{n}", "row": n, "title": title,
                        "party": party, "kind": cls["kind"], "label": nice(header[rl["dates"][0]]), "amount": None,
                        "currency": None, "date": dates[0], "text": text[:1000], "ref": ref,
                        "direction": cls.get("direction"), "fields": fields})
    return out
