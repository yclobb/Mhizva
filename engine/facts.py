"""Facts: the numbers and dates inside documents, kept as rows so code (not the model) can total,
compare, tabulate and chart them across many documents.

At upload each document gets facts: one row per sentence that carries an amount or a date.
    {"doc": path, "title": ..., "party": ..., "kind": ..., "label": ..., "amount": float|None,
     "date": "YYYY-MM-DD"|None, "text": sentence}
All rows live in <library>/facts.jsonl (rebuilt per document on upload).
"""
import datetime as dt
import io
import json
import os
import re
import zipfile

from .llm import chat, has_cjk, words

FACTS = "facts.jsonl"
KINDS = ["contract", "quote", "invoice", "receipt", "insurance", "policy", "report", "other"]
LETTERS = "ABCDEFGH"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
MON = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}

AMOUNT = re.compile(r"(?<![\w.])\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(\s?[kKmM]\b)?")
# Chinese money: ¥1,200 / ￥1200 / RMB 1200 / 人民币1200元 / 1.5万元 / 3000元
AMOUNT_CN = re.compile(r"(?:[¥￥]|RMB\s?|CNY\s?|人民币\s?)(\d[\d,]*(?:\.\d+)?)\s?(万|千)?\s?元?"
                       r"|(\d[\d,]*(?:\.\d+)?)\s?(万|千)?\s?元")
DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*[日号])?")
SYMBOL = {"AUD": "$", "CNY": "¥"}
DATE_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?,?\s+(\d{4})\b", re.I)
DATE_MDY = re.compile(r"\b(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")  # Australian day/month/year
DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
SENT = re.compile(r"(?<=[.!?])\s+|(?<=[。！？；])|\n+")


def parse_amount(m):
    v = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
    suf = (m.group(3) or "").strip().lower()
    return v * (1000 if suf == "k" else 1_000_000 if suf == "m" else 1)


def find_amounts(s):
    """[(value, currency, matched text)] for $ (AUD) and ¥/元/RMB (CNY) amounts in a sentence."""
    out = [(parse_amount(m), "AUD", m.group(0)) for m in AMOUNT.finditer(s)]
    for m in AMOUNT_CN.finditer(s):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        v = float(num.replace(",", "")) * (10_000 if unit == "万" else 1_000 if unit == "千" else 1)
        out.append((v, "CNY", m.group(0)))
    return out


def parse_dates(s):
    out = []
    for m in DATE_DMY.finditer(s):
        out.append((int(m.group(3)), MON[m.group(2)[:3].lower()], int(m.group(1))))
    for m in DATE_MDY.finditer(s):
        out.append((int(m.group(3)), MON[m.group(1)[:3].lower()], int(m.group(2))))
    for m in DATE_NUM.finditer(s):
        out.append((int(m.group(3)), int(m.group(2)), int(m.group(1))))
    for m in DATE_ISO.finditer(s):
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in DATE_CN.finditer(s):  # 2026年3月5日; a month alone (2026年3月) counts as its 1st
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)))
    good = []
    for y, mo, d in out:
        try:
            good.append(dt.date(y, mo, d).isoformat())
        except ValueError:
            pass
    return good


def label_of(sentence, amount_text):
    """A few words saying what the number is: the words right before it ('Annual premium', 'Total quote')."""
    before = sentence.split(amount_text)[0] if amount_text else sentence
    before = re.split(r"[.;，,。；]", before)[-1]
    cn = re.findall(r"[一-鿿]+", before)
    if cn:  # Chinese: the last few characters before the number (合同总价, 年保费)
        return "".join(cn)[-6:]
    ws = re.findall(r"[A-Za-z][A-Za-z\-']*", before)
    ws = [w for w in ws if w.lower() not in ("is", "of", "the", "a", "an", "and", "for", "at", "was", "are")]
    return " ".join(ws[-4:]) or "amount"


def extract(llm, path, title, text):
    party = llm.write(chat(
        "You read business documents.",
        f"Document: {title}\n{text[:2500]}\n\nWho is the main other party (customer, supplier, insurer, person)? "
        f"Reply with the name only, or 'none'."), n=12, stop=["<|eot_id|>", "\n"]).strip(" .\"'")
    if not party or party.lower() in ("none", "n/a", "unknown"):
        party = title
    probs = llm.pick(chat(
        "You classify business documents.",
        f"Document: {title}\n{text[:1500]}\n\nTypes:\n" + "\n".join(f"{LETTERS[i]}) {k}" for i, k in enumerate(KINDS))
        + "\n\nWhich type is this document? Answer with one letter."), list(LETTERS[:len(KINDS)]))
    kind = KINDS[LETTERS.index(max(probs, key=probs.get))]
    rows = []
    for s in SENT.split(text):
        s = s.strip(" -*#\t")
        if not s:
            continue
        dates = parse_dates(s)
        amounts = find_amounts(s)
        for value, currency, matched in amounts:
            rows.append({"doc": path, "title": title, "party": party, "kind": kind,
                         "label": label_of(s, matched), "amount": value, "currency": currency,
                         "date": dates[0] if dates else None, "text": s[:300]})
        if not amounts:
            for d in dates:
                rows.append({"doc": path, "title": title, "party": party, "kind": kind,
                             "label": label_of(s, None), "amount": None, "date": d, "text": s[:300]})
    # Amount and date are often in different sentences ("Amount due: $16,800. Due date: 14 April 2026.").
    # An amount with no date of its own takes the document's first date, marked as such. Date-only rows
    # are kept (start/end dates matter) but select() hides them when their document has amount rows.
    doc_dates = [r["date"] for r in rows if r["date"]]
    ref = REF.search(text)
    for r in rows:
        r["ref"] = ref.group(0) if ref else None
        if r["amount"] is not None:
            r["date_from"] = "sentence" if r["date"] else ("document" if doc_dates else None)
            r["date"] = r["date"] or (doc_dates[0] if doc_dates else None)
    return rows


class FactStore:
    def __init__(self, root):
        self.path = os.path.join(root, FACTS)

    def all(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            return []

    def replace_doc(self, doc_prefix, rows):
        """Drop rows of this document (or its sections, for long documents) and add the new ones."""
        keep = [r for r in self.all() if not (r["doc"] == doc_prefix or r["doc"].startswith(doc_prefix + "/"))]
        with open(self.path, "w", encoding="utf-8") as f:
            for r in keep + rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---- question routing
OVER_TIME = r"\b(over time|by month|per month|monthly|trend|timeline)\b|按月|每月|随时间|趋势|走势"


def question_kind(question):
    q = question.lower()
    if re.search(r"\b(trends?|going (up|down)|increas\w*|decreas\w*|rising|falling|grow\w*|shrink\w*|"
                 r"changed?|changing|getting (more|less|worse|better)|over time)\b"
                 r"|趋势|走势|上涨|涨价|增加|增长|下降|下跌|减少|变化|越来越", q):
        return "trend"
    if re.search(r"\b(chart|graph|plot|visuali[sz]e)\b|图表|画图|柱状图|折线图|图", q):
        return "chart"
    if re.search(r"\b(table|spreadsheet|excel|list (all|every|each)|list the|show (all|every))\b"
                 r"|表格|电子表格|列出|清单|列表|所有", q):
        return "table"
    if re.search(r"\b(total|sum|altogether|in all|combined|average|how many|add up|overall)\b"
                 r"|总共|合计|一共|总额|总计|平均|几个|多少个|多少份", q):
        return "aggregate"
    return "lookup"


def date_window(question, today=None):
    """('2026-03-01', '2026-03-31') style bounds from 'this month', 'next month', 'in March', '2026'."""
    today = today or dt.date.today()
    q = question.lower()

    def month(y, m):
        end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        return dt.date(y, m, 1).isoformat(), end.isoformat()
    if "this month" in q or "本月" in q or "这个月" in q:
        return month(today.year, today.month)
    if "next month" in q or "下个月" in q or "下月" in q:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return month(y, m)
    if "last month" in q or "上个月" in q or "上月" in q:
        y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        return month(y, m)
    if "this year" in q or "今年" in q:
        return f"{today.year}-01-01", f"{today.year}-12-31"
    year = re.search(r"(?<!\d)(20\d{2})(?!\d)", q)
    for i, name in enumerate(MONTHS):
        if re.search(rf"\b{name}\b", q):
            return month(int(year.group(1)) if year else today.year, i + 1)
    cn_month = re.search(r"(?<!\d)(1[0-2]|[1-9])\s*月", q)  # 4月 / 2026年4月
    if cn_month:
        return month(int(year.group(1)) if year else today.year, int(cn_month.group(1)))
    if year:
        return f"{year.group(1)}-01-01", f"{year.group(1)}-12-31"
    return None


GENERIC = set(words("total sum altogether combined average how many much list all every each table spreadsheet "
                    "chart graph plot show me our we us by per over time month year this next last amount amounts "
                    "cost costs value values price prices "
                    # trend wording and roles describe the question, not its subject
                    "trend trends increasing decreasing rising falling going up down growing shrinking change "
                    "changed changing getting worse better expensive cheaper over time "
                    "supplier suppliers vendor vendors customer customers client clients their which who "
                    "paid pay spend spent owe owed owing have has "
                    "everything anything"))
# Chinese question wording, removed from the question text before it is cut into character pairs
# (otherwise 哪些供应商 would leave the pair 些供 behind as a fake subject).
CN_GENERIC = sorted(("总共 合计 一共 总额 总计 平均 多少钱 多少 几个 列出 所有 表格 电子表格 清单 图表 画图 柱状图 "
                     "折线图 趋势 走势 上涨 涨价 增加 增长 下降 下跌 减少 变化 越来越 按月 每月 本月 这个月 下个月 "
                     "上个月 今年 金额 价格 费用 成本 付款 支付 欠款 供应商 客户 我们 哪些 哪个 哪家 显示 一下 要收 "
                     "要付 到期 吗 年 月 日").split(), key=len, reverse=True)


def subject_words(question):
    q = question
    for phrase in CN_GENERIC:
        q = q.replace(phrase, " ")
    # dates are handled by date_window, so years and month names are not subject words
    q = re.sub(r"\b(" + "|".join(MONTHS + [m[:3] for m in MONTHS]) + r")\b|(?<!\d)20\d{2}(?!\d)", " ", q, flags=re.I)
    return set(words(q)) - GENERIC
WHO = r"\bwhich\b|\bwho\b|哪些|哪个|哪家|谁"


def select(rows, question):
    """Rows relevant to the question: subject words must meet the row; dates must fall in the window."""
    q = subject_words(question)
    win = date_window(question)
    out = []
    for r in rows:
        hay = set(words(f"{r['title']} {r['party']} {r['kind']} {r['label']} {r['text']}"))
        if q and not (q & hay):
            continue
        if win and not (r["date"] and win[0] <= r["date"] <= win[1]):
            continue
        out.append(r)
    want_money = bool(re.search(r"\b(how much|total|sum|cost|price|owe|pay|spend|spent|worth)|\$"
                                r"|多少钱|金额|总额|费用|价格|成本|付|欠|元|¥|￥", question.lower()))
    with_money = {r["doc"] for r in out if r["amount"] is not None}
    if want_money:
        out = [r for r in out if r["amount"] is not None]
    else:  # a document with amounts is shown by its amount rows, not again by its bare dates
        out = [r for r in out if r["amount"] is not None or r["doc"] not in with_money]
    # the same record in several files (a copy, a reminder) counts once
    out.sort(key=lambda r: r["doc"])
    return dedupe(out)


REF = re.compile(r"\b[A-Z]{2,5}-?\d{3,}\b")  # invoice / quote / policy numbers like INV-1043


def dedupe(rows):
    """Count each record once. Across documents, rows with the same party, label, amount and date
    (an invoice and its copy or reminder), or the same reference number and amount, are one record.
    Merged rows are marked so coverage can report them."""
    seen, out = set(), Rows()
    for r in rows:
        keys = [("same", r["party"].lower(), r["label"].lower(), r["amount"], r["date"])]
        if r.get("ref") and r["amount"] is not None:
            keys.append(("ref", r["ref"], r["amount"]))
        if any(k in seen for k in keys):
            out.merged += 1
            continue
        seen.update(keys)
        out.append(r)
    return out


class Rows(list):
    """A list of fact rows that remembers how many duplicates were merged away."""
    merged = 0


def best_per_doc(rows):
    """For totals: one amount per document, preferring a row labelled total/price/premium, else the largest."""
    pick = {}
    for r in rows:
        if r["amount"] is None:
            continue
        score = (bool(re.search(r"total|price|premium|amount due|balance|owing", r["label"].lower())), r["amount"])
        if r["doc"] not in pick or score > pick[r["doc"]][0]:
            pick[r["doc"]] = (score, r)
    return [v[1] for v in pick.values()]


def money(v, currency="AUD"):
    return f"{SYMBOL.get(currency, '$')}{v:,.2f}".replace(".00", "")


def cur(r):
    return r.get("currency") or "AUD"


MONTHS_CN = [f"{i}月" for i in range(1, 13)]


def nice_date(d, zh=False):
    y, m, dd = d.split("-")
    return f"{y}年{int(m)}月{int(dd)}日" if zh else f"{int(dd)} {MONTHS[int(m) - 1][:3].capitalize()} {y}"


def mon(k, zh=False):
    return f"{k[:4]}年{int(k[5:])}月" if zh else f"{MONTHS[int(k[5:]) - 1][:3].capitalize()} {k[:4]}"


# Answer wording, English and Chinese (chosen by the question's language).
T = {
    "en": {"Document": "Document", "Who": "Who", "What": "What", "Amount": "Amount", "Date": "Date",
           "Trend": "Trend", "Change": "Change", "From": "From", "To": "To",
           "rising": "rising", "falling": "falling", "flat": "flat",
           "chart": "Here's a {type} chart from {docs}.", "bar": "bar", "line": "line",
           "table": "I found {items} in {docs}.", "total": "Total: {value} across {docs}.",
           "average": "Average: {value} across {docs}.", "match": "{docs} match.",
           "trend": "{Word}: {first} in {m1} to {last} in {m2} ({pct}), over {months} and {docs}.",
           "rising_who": "Rising: {names}.", "none_rising": "Nothing is clearly rising.",
           "based": " Based on {n} with at least two months of dated amounts.",
           "also": " Other currencies left out: {others}."},
    "zh": {"Document": "文件", "Who": "对象", "What": "项目", "Amount": "金额", "Date": "日期",
           "Trend": "趋势", "Change": "变化", "From": "开始", "To": "结束",
           "rising": "上升", "falling": "下降", "flat": "持平",
           "chart": "这是根据 {docs} 生成的{type}。", "bar": "柱状图", "line": "折线图",
           "table": "在 {docs} 中找到 {items}。", "total": "合计：{value}，共 {docs}。",
           "average": "平均：{value}，共 {docs}。", "match": "有 {docs} 符合。",
           "trend": "{Word}：从{m1}的 {first} 到{m2}的 {last}（{pct}），共 {months}、{docs}。",
           "rising_who": "上升的有：{names}。", "none_rising": "没有明显上升的。",
           "based": " 依据 {n} 至少两个月的带日期金额。",
           "also": " 未计入其他币种：{others}。"},
}


def count(n, en_word, zh=False, zh_unit="份文件"):
    if zh:
        return f"{n} {zh_unit}"
    return f"{n} {en_word}{'s' * (n != 1)}" if not en_word.endswith("y") or n == 1 else f"{n} {en_word[:-1]}ies"


def columns(zh, currency):
    t = T["zh" if zh else "en"]
    return [{"name": t["Document"], "type": "text"}, {"name": t["Who"], "type": "text"},
            {"name": t["What"], "type": "text"}, {"name": t["Amount"], "type": "money", "currency": currency},
            {"name": t["Date"], "type": "date"}]


def table_of(rows, zh=False):
    """Typed table. rows[i] = raw values (money = number in the column's currency, date = 'YYYY-MM-DD',
    missing = null); text[i] = the same row formatted for display; sources[i] = where row i came from
    (row-aligned: every row is one fact from one document, so the row's source is every cell's source).
    The money column's currency is "mixed" when rows differ; each display cell then shows its own symbol."""
    rows = sorted(rows, key=lambda r: (r["date"] or "9999", r["title"]))
    currencies = {cur(r) for r in rows if r["amount"] is not None}
    currency = currencies.pop() if len(currencies) == 1 else ("mixed" if currencies else "AUD")
    return {"columns": columns(zh, currency),
            "rows": [[r["title"], r["party"], r["label"], r["amount"], r["date"]] for r in rows],
            "text": [[r["title"], r["party"], r["label"], money(r["amount"], cur(r)) if r["amount"] is not None else "",
                      nice_date(r["date"], zh) if r["date"] else ""] for r in rows],
            "sources": [[{"path": r["doc"], "quote": r["text"]}] for r in rows]}


def chart_of(table, question, zh=False):
    """Chart over the table's own rows; points[i].rows lists the table rows summed into point i."""
    over_time = bool(re.search(OVER_TIME, question.lower()))
    buckets, order = {}, []
    for i, (title, party, label, amount, date) in enumerate(table["rows"]):
        if amount is None or (over_time and not date):
            continue
        k = date[:7] if over_time else party
        if k not in buckets:
            buckets[k] = {"value": 0.0, "rows": []}
            order.append(k)
        buckets[k]["value"] += amount
        buckets[k]["rows"].append(i)
    if over_time:
        order.sort()
    else:
        order.sort(key=lambda k: -buckets[k]["value"])
    return {"type": "line" if over_time else "bar",
            "y": {"name": T["zh" if zh else "en"]["Amount"], "unit": table["columns"][3]["currency"]},
            "points": [{"x": mon(k, zh) if over_time else k, "value": round(buckets[k]["value"], 2),
                        "rows": buckets[k]["rows"]} for k in order]}


def coverage(all_rows, used_rows, merged=0, excluded=None):
    """What the answer is based on, so a chart from a subset says so."""
    dates = sorted(r["date"] for r in used_rows if r["date"])
    lib_docs = {r["doc"] for r in all_rows}
    used_docs = {r["doc"] for r in used_rows}
    return {"documents_with_facts": len(lib_docs), "documents_used": len(used_docs),
            "subset": len(used_docs) < len(lib_docs),
            "date_range": [dates[0], dates[-1]] if dates else None,
            "missing_dates": sum(1 for r in used_rows if not r["date"]),
            "dates_from_document": sum(1 for r in used_rows if r.get("date_from") == "document"),
            "duplicates_merged": merged,
            "currency": sorted({cur(r) for r in used_rows if r["amount"] is not None}) or ["AUD"],
            "excluded_other_currency": excluded or {}}


def month_series(rows):
    """{'YYYY-MM': total} over dated money rows."""
    out = {}
    for r in rows:
        if r["amount"] is not None and r["date"]:
            out[r["date"][:7]] = out.get(r["date"][:7], 0.0) + r["amount"]
    return dict(sorted(out.items()))


def direction(series):
    """(word, percent change first->last, slope per month) from a least-squares fit over the months."""
    ys = list(series.values())
    n = len(ys)
    xs = [int(k[:4]) * 12 + int(k[5:]) for k in series]
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    pct = (ys[-1] - ys[0]) / ys[0] * 100 if ys[0] else 0.0
    rel = slope / my * 100 if my else 0.0  # slope as % of the average month
    word = "rising" if rel > 3 else "falling" if rel < -3 else "flat"
    return word, pct, slope


def trend(picked, question, zh=False):
    t = T["zh" if zh else "en"]
    money_rows = [r for r in picked if r["amount"] is not None and r["date"]]
    currency = cur(money_rows[0]) if money_rows else "AUD"
    if re.search(WHO, question.lower()):
        # per party: who is rising / falling, from parties with at least two dated months
        groups = {}
        for r in money_rows:
            groups.setdefault(r["party"], []).append(r)
        found = []
        for party, rs in groups.items():
            s = month_series(rs)
            if len(s) >= 2:
                w, pct, _ = direction(s)
                found.append((party, w, pct, s, rs))
        if not found:
            return None
        found.sort(key=lambda f: -abs(f[2]))
        used = [r for f in found for r in f[4]]
        table = {"columns": [{"name": t["Who"], "type": "text"}, {"name": t["Trend"], "type": "text"},
                             {"name": t["Change"], "type": "percent"}, {"name": t["From"], "type": "text"},
                             {"name": t["To"], "type": "text"}],
                 "rows": [[p, t[w], round(pct, 1), mon(list(s)[0], zh), mon(list(s)[-1], zh)] for p, w, pct, s, _ in found],
                 "text": [[p, t[w], f"{pct:+.0f}%", mon(list(s)[0], zh), mon(list(s)[-1], zh)] for p, w, pct, s, _ in found],
                 "sources": [[{"path": r["doc"], "quote": r["text"]} for r in rs] for *_, rs in found],
                 "trend_codes": [w for _, w, *_ in found]}  # "rising"|"falling"|"flat", language-independent
        rising = [f[0] for f in found if f[1] == "rising"]
        sep = "、" if zh else ", "
        text = (t["rising_who"].format(names=sep.join(rising)) if rising else t["none_rising"]) + \
            t["based"].format(n=count(len(found), "party", zh, "个对象"))
        return text, table, None, used
    s = month_series(money_rows)
    if len(s) < 2:
        return None
    w, pct, _ = direction(s)
    keys = list(s)
    table = table_of(money_rows, zh)
    chart = chart_of(table, "over time", zh)
    word = t[w]
    text = t["trend"].format(Word=word.capitalize(), first=money(s[keys[0]], currency), m1=mon(keys[0], zh),
                             last=money(s[keys[-1]], currency), m2=mon(keys[-1], zh), pct=f"{pct:+.0f}%",
                             months=count(len(keys), "month", zh, "个月"),
                             docs=count(len({r['doc'] for r in money_rows}), "document", zh))
    return text, table, chart, money_rows


def one_currency(picked):
    """Amounts in different currencies are never added together: keep the most common currency's rows
    (plus rows without amounts) and report what was left out."""
    from collections import Counter
    c = Counter(cur(r) for r in picked if r["amount"] is not None)
    if len(c) <= 1:
        return picked, {}
    main = c.most_common(1)[0][0]
    kept = Rows(r for r in picked if r["amount"] is None or cur(r) == main)
    kept.merged = picked.merged
    return kept, {k: v for k, v in c.items() if k != main}


def answer(rows, question, kind):
    """(answer text, table, chart, coverage) for aggregate / table / chart / trend questions,
    or None if nothing matches. Always computed over the whole facts table, not a search budget.
    The answer is in Chinese when the question is."""
    zh = has_cjk(question)
    picked = select(rows, question)
    if not picked:
        return None
    excluded = {}
    if kind != "table":  # a table may list mixed currencies; sums, charts and trends may not
        picked, excluded = one_currency(picked)
    note = T["zh" if zh else "en"]["also"].format(
        others=", ".join(f"{n} {k}" for k, n in excluded.items())) if excluded else ""
    if kind == "trend":
        res = trend(picked, question, zh)
        if not res:
            return None
        text, table, chart, used = res
        return text + note, table, chart, coverage(rows, used, picked.merged, excluded)
    res = answer_basic(picked, question, kind, zh)
    if not res:
        return None
    text, table, chart = res
    used_docs = {s[0]["path"] for s in table["sources"]}
    return text + note, table, chart, coverage(rows, [r for r in picked if r["doc"] in used_docs],
                                               picked.merged, excluded)


def answer_basic(picked, question, kind, zh=False):
    t = T["zh" if zh else "en"]
    if kind == "chart":
        money_rows = [r for r in picked if r["amount"] is not None]
        over_time = re.search(OVER_TIME, question.lower())
        table = table_of(money_rows if over_time else best_per_doc(picked), zh)
        chart = chart_of(table, question, zh)
        if not chart["points"]:
            return None
        docs = len({s[0]["path"] for s in table["sources"]})
        return t["chart"].format(type=t[chart["type"]], docs=count(docs, "document", zh)), table, chart
    if kind == "table":
        table = table_of(picked, zh)
        docs = len({r["doc"] for r in picked})
        return t["table"].format(items=count(len(picked), "matching item", zh, "项"),
                                 docs=count(docs, "document", zh)), table, None
    per_doc = best_per_doc(picked)
    if per_doc and not re.search(r"\bhow many\b|几个|多少个|多少份", question.lower()):
        avg = "average" in question.lower() or "平均" in question
        total = sum(r["amount"] for r in per_doc)
        value = total / len(per_doc) if avg else total
        return (t["average" if avg else "total"].format(value=money(value, cur(per_doc[0])),
                                                         docs=count(len(per_doc), "document", zh)),
                table_of(per_doc, zh), None)
    docs = len({r["doc"] for r in picked})
    return t["match"].format(docs=count(docs, "document", zh)), table_of(picked, zh), None


# ---- spreadsheet export (standard library only)
def literal(v):
    """Document text must never run as a spreadsheet formula (=, +, -, @, tab, CR): prefix an apostrophe,
    except for plain numbers and amounts like -12.50 or -$1,200."""
    v = str(v)
    if v[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"-?[$¥]?-?[\d,]+(\.\d+)?%?", v):
        return "'" + v
    return v


def to_csv(table):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([literal(c["name"]) for c in table["columns"]])
    w.writerows([[literal(v) for v in row] for row in table["text"]])
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(table):
    def cell(ref, v):
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        v = str(v)
        esc = v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<c r="{ref}" t="inlineStr"><is><t>{esc}</t></is></c>'

    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    # numbers stay numbers so the spreadsheet can sum them; dates go in as readable text
    date_cols = {i for i, c in enumerate(table["columns"]) if c["type"] == "date"}
    body = [[t if i in date_cols else v for i, (v, t) in enumerate(zip(raw, txt))]
            for raw, txt in zip(table["rows"], table["text"])]
    rows = [[c["name"] for c in table["columns"]]] + body
    sheet = "".join(f'<row r="{ri + 1}">' + "".join(cell(f"{col(ci)}{ri + 1}", v) for ci, v in enumerate(row)) + "</row>"
                    for ri, row in enumerate(rows))
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Mhizva" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet}</sheetData></worksheet>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
