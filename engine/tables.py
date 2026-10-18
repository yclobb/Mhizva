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

from .facts import REF, parse_dates

TABULAR = (".csv", ".tsv", ".xlsx")
ROWS_PER_SECTION = 25

MONEY_COL = re.compile(r"total|amount|subtotal|tax|gst|vat|price|cost|balance|fee|paid|due_usd|due_aud|"
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
    sample = rows[:50]

    def numeric(i):
        vals = [r[i] for r in sample if i < len(r) and r[i].strip()]
        return vals and sum(1 for v in vals if to_number(v) is not None) >= 0.8 * len(vals)
    money = [i for i, h in enumerate(header) if MONEY_COL.search(h) and not NOT_MONEY.search(h) and numeric(i)]
    dates = sorted([i for i, h in enumerate(header) if DATE_COL.search(h)],
                   key=lambda i: (0 if re.search(r"invoice|issue|开票", header[i], re.I) else 1, i))
    party = None
    for word in PARTY_COLS:
        party = next((i for i, h in enumerate(header) if word in h.lower() and i not in money), None)
        if party is not None:
            break
    ref = next((i for i, h in enumerate(header) if REF_COL.search(h) and i not in money), None)
    # other numeric columns (Close, Volume, Units, Score) are measures: analysable, but not money
    measures = [i for i, h in enumerate(header) if i not in money and i not in dates and i != ref and numeric(i)
                and not ID_COL.search(h)]
    return {"money": money, "dates": dates, "party": party, "ref": ref, "measures": measures}


def to_number(v):
    s = re.sub(r"[,\s$¥￥€£]|(?i:usd|aud|cny|rmb)", "", str(v))
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def to_date(v):
    v = str(v).strip()
    found = parse_dates(v)
    if found:
        return found[0]
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


def facts_from_rows(path_of_row, title, header, rows, cls):
    """One fact per money cell. record = file + row number, so totals count each row once."""
    rl = roles(header, rows)
    default_cur = currency_of(" ".join(header))
    out = []
    for n, row in enumerate(rows, 1):
        cell = lambda i: row[i].strip() if i is not None and i < len(row) else ""
        dates = [d for d in (to_date(cell(i)) for i in rl["dates"]) if d]
        party = cell(rl["party"]) or cls["party"]
        text = record_text(header, row)
        m = REF.search(text)
        ref = cell(rl["ref"]) if rl["ref"] is not None else (m.group(0) if m else None)
        fields = {nice(h): cell(i) for i, h in enumerate(header) if cell(i)}
        for i in rl["money"] + rl["measures"]:
            v = to_number(cell(i))
            if v is None:
                continue
            out.append({"doc": path_of_row(n), "record": f"{path_of_row(n)}#{n}", "row": n, "title": title,
                        "party": party, "kind": cls["kind"], "label": nice(header[i]), "amount": v,
                        "currency": currency_of(header[i], default_cur) if i in rl["money"] else "",
                        "date": dates[0] if dates else None,
                        "date_from": "row" if dates else None, "text": text[:1000], "ref": ref,
                        "direction": cls.get("direction"), "fields": fields})
        if not rl["money"] and not rl["measures"] and dates:
            out.append({"doc": path_of_row(n), "record": f"{path_of_row(n)}#{n}", "row": n, "title": title,
                        "party": party, "kind": cls["kind"], "label": nice(header[rl["dates"][0]]), "amount": None,
                        "currency": None, "date": dates[0], "text": text[:1000], "ref": ref,
                        "direction": cls.get("direction"), "fields": fields})
    return out
