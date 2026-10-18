"""Upload path: convert a file to markdown, describe it, file it into the tree, roll summaries up.

The heavy work happens here, once per document (optimisation 3), so questions stay fast.
"""
import html
import io
import os
import re
import zipfile
from collections import Counter

from . import facts, tables
from .llm import chat, plain_words
from .tree import Entry, safe_name

LONG = 6000        # characters; longer documents become their own subtree
SECTION = 3000     # target section size inside a long document
READ_CAP = 6000    # characters of a document the model sees when describing it (fits 4k context)
MAX_CHILDREN = 16  # per folder
LABELS = "ABCDEFGHIJKLMNOP"


# ---- conversion
def to_markdown(filename, data):
    ext = os.path.splitext(filename)[1].lower()
    title = os.path.splitext(os.path.basename(filename))[0]
    if ext in (".md", ".txt", ".csv", ".log", ""):
        return title, data.decode("utf-8", "replace")
    if ext in (".html", ".htm"):
        t = re.sub(r"(?is)<(script|style).*?</\1>", "", data.decode("utf-8", "replace"))
        t = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>|</tr>", "\n", t)
        return title, html.unescape(re.sub(r"<[^>]+>", "", t))
    if ext == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        paras = []
        for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
            heading = re.search(r'<w:pStyle w:val="Heading(\d)"', p)
            if text.strip():
                paras.append(("#" * int(heading.group(1)) + " " if heading else "") + html.unescape(text))
        return title, "\n\n".join(paras)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("PDF support needs the pypdf package")
        pages = PdfReader(io.BytesIO(data)).pages
        return title, "\n\n".join((p.extract_text() or "") for p in pages)
    raise ValueError(f"unsupported file type {ext}")


def split_sections(md):
    """Split on headings if there are any, else on paragraphs; pieces are grouped up to ~SECTION
    characters, and any single piece longer than that is cut at sentence ends, so no text is lost."""
    parts = re.split(r"(?m)^(?=#{1,3} )", md)
    if len(parts) < 2:
        parts = re.split(r"\n\s*\n", md)
    pieces = []
    for p in parts:
        while len(p) > SECTION:
            cut = max(p.rfind(". ", 0, SECTION), p.rfind("\n", 0, SECTION)) + 1 or SECTION
            pieces.append(p[:cut])
            p = p[cut:]
        pieces.append(p)
    out, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) > SECTION:
            out.append(cur.strip())
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        out.append(cur.strip())
    return out


def top_keywords(text, n=10):
    c = Counter(w for w in plain_words(text) if not w.isdigit() or len(w) == 4)
    return [w for w, _ in c.most_common(n)]


# ---- description (model)
def describe(llm, title, text):
    excerpt = text[:READ_CAP]
    summary = llm.write(chat(
        "You write one-line catalogue entries for business documents. Be specific: names, amounts, dates.",
        f"Document: {title}\n{excerpt}\n\nWrite one line (max 25 words) saying what this document is and its key facts."),
        n=60, stop=["<|eot_id|>", "\n"])
    qs = llm.write(chat(
        "You write short questions a business owner might ask that this document answers.",
        f"Document: {title}\n{excerpt}\n\nWrite 3 questions, one per line, no numbering."),
        n=90, stop=["<|eot_id|>", "\n\n"])
    questions = [q.strip(" -0123456789.") for q in qs.splitlines() if q.strip()][:3]
    return summary or title, top_keywords(title + " " + text), questions


def summarise_folder(llm):
    def run(title, entries):
        if not entries:
            return "Empty."
        listing = "\n".join(f"- {e.name}: {e.summary}" for e in entries[:MAX_CHILDREN])
        return llm.write(chat(
            "You write one-line catalogue entries for folders. Name the main subjects, people and companies.",
            f"Folder: {title}\n{listing}\n\nWrite one line (max 25 words) describing what this folder holds."),
            n=60, stop=["<|eot_id|>", "\n"]) or title
    return run


# ---- filing
def menu(entries):
    return "\n".join(f"{LABELS[i]}) {e.name}: {e.summary}" for i, e in enumerate(entries))


def choose_folder(lib, llm, title, summary):
    """Walk down from the root choosing a folder, or create a new one where nothing fits.

    "Make a new folder" is only offered while the folder has room (fewer than MAX_CHILDREN entries),
    so indexes stay small. The one exception: a folder already full of documents with no subfolders
    gets one more (a subfolder) rather than refusing the file; self-organisation tidies that later."""
    folder = ""
    for _ in range(4):
        entries = lib.entries(folder)
        subs = [e for e in entries if e.kind == "folder"][:len(LABELS)]
        room = len(entries) < MAX_CHILDREN
        if room:
            subs = subs[:len(LABELS) - 1]
        new_label = LABELS[len(subs)] if room else None
        if subs:
            prompt = chat(
                "You file documents into folders.",
                f"Document: {title}: {summary}\n\nFolders:\n{menu(subs)}\n"
                + (f"{new_label}) None of these, make a new folder\n" if room else "")
                + "\nWhich folder should this document go in? Answer with one letter.")
            probs = llm.pick(prompt, list(LABELS[:len(subs) + (1 if room else 0)]))
            best = max(probs, key=probs.get)
            if best != new_label:
                folder = f"{folder}/{subs[LABELS.index(best)].name}".lstrip("/")
                continue
        if not room or folder == "":
            name = llm.write(chat(
                "You name folders for a small business filing system. Two or three words, title case.",
                f"Document: {title}: {summary}\n\nWhat folder name should hold documents like this? Reply with the folder name only."),
                n=10, stop=["<|eot_id|>", "\n"])
            name = safe_name(name.strip(" .\"'"))
            sub = f"{folder}/{name}".lstrip("/")
            if not lib.is_folder(sub):
                lib.write_index(sub, name, summary, [])
                lib.put_entry(folder, Entry(name, "folder", summary, []))
            folder = sub
        break
    return folder


def add_sections(lib, llm, folder, title, meta, sections, start):
    for i, sec in enumerate(sections, start + 1):
        head = re.match(r"#{1,3} (.+)", sec)
        sec_title = f"{title}, part {i}" + (f": {head.group(1).strip()}" if head else "")
        s_sum, s_kw, s_q = describe(llm, sec_title, sec)
        s_name = f"{i:03d} {safe_name(head.group(1) if head else 'Part')}.md"
        lib.write_doc(folder, s_name, {**meta, "title": sec_title, "summary": s_sum,
                                        "keywords": s_kw, "questions": s_q, "part": i}, sec)
        lib.put_entry(folder, Entry(s_name, "doc", s_sum, s_kw))


def place_sections(lib, llm, roll, folder, title, meta, summary, keywords, sections, start):
    """Put sections into `folder`; more than MAX_CHILDREN become at most MAX_CHILDREN "Part" folders,
    recursively, so no index ever holds more than MAX_CHILDREN entries however long the document."""
    if len(sections) <= MAX_CHILDREN:
        add_sections(lib, llm, folder, title, meta, sections, start)
        return
    size = -(-len(sections) // MAX_CHILDREN)  # ceiling division: at most MAX_CHILDREN groups
    for g, j in enumerate(range(0, len(sections), size)):
        home = f"{folder}/Part {g + 1}"
        lib.write_index(home, f"{title}, part {g + 1}", summary, [])
        lib.put_entry(folder, Entry(f"Part {g + 1}", "folder", summary, keywords))
        place_sections(lib, llm, roll, home, title, meta, summary, keywords, sections[j:j + size], start + j)
        lib.refresh_up(home, roll)


def doc_paths(lib, path):
    if not lib.is_folder(path):
        return [path]
    out = []
    for e in lib.entries(path):
        out += doc_paths(lib, f"{path}/{e.name}")
    return out


def store_facts(lib, llm, path, cls):
    """Amounts and dates of the new document (every section, for a long one) into the facts table."""
    rows = []
    for p in doc_paths(lib, path):
        meta, text = lib.read_doc(p)
        rows += facts.extract(llm, p, meta.get("title") or p, text, cls)
    facts.FactStore(lib.root).replace_doc(path, rows)


def ensure_folder(lib, parent, name, summary):
    sub = f"{parent}/{name}".lstrip("/")
    if not lib.is_folder(sub):
        lib.write_index(sub, name, summary, [])
        lib.put_entry(parent, Entry(name, "folder", summary, []))
    return sub


def file_folder(lib, llm, cls, title, summary):
    """Type first, then who it's with: Invoices/Smith Family, Bills/Timber Co, Policies/.
    Decided by the document's classification, not invented by the model, so the tree stays tidy
    with small models. Falls back to the model's folder choice when a type folder is full."""
    top_name = facts.KIND_FOLDER[cls["kind"]]
    if top_name not in {e.name for e in lib.entries("")} and len(lib.entries("")) >= MAX_CHILDREN:
        return choose_folder(lib, llm, title, summary)
    top = ensure_folder(lib, "", top_name, summary)
    party = cls["party"]
    if party == title:  # no separate party (a policy, a report): file directly under the type
        return top
    name = safe_name(party)
    if lib.is_folder(f"{top}/{name}") or len(lib.entries(top)) < MAX_CHILDREN:
        return ensure_folder(lib, top, name, summary)
    return top


def add_table(lib, llm, filename, data):
    """A spreadsheet: one folder for the file, one section doc per 25 rows (summaries written by code, not
    the model, so 10,000 rows file in seconds), and one fact per money cell straight from the columns."""
    title = os.path.splitext(os.path.basename(filename))[0]
    header, rows = tables.read_table(filename, data)
    if not header or not rows:
        raise ValueError("no rows found in this spreadsheet")
    rl = tables.roles(header, rows)
    cols = ", ".join(tables.nice(h) for h in header)
    opening = f"{title}\n{cols}\n" + "\n".join(tables.record_text(header, r) for r in rows[:3])
    kind = facts.kind_by_words(opening) or "report"
    cls = {"party": title, "kind": kind, "direction": facts.DIRECTION.get(kind)}
    parties = sorted({r[rl["party"]].strip() for r in rows if rl["party"] is not None and rl["party"] < len(r)} - {""})
    summary = (f"Spreadsheet, {len(rows)} rows. Columns: {cols}."
               + (f" Includes {', '.join(parties[:6])}{' and more' if len(parties) > 6 else ''}." if parties else ""))
    keywords = top_keywords(title + " " + cols + " " + " ".join(parties), 12)
    folder = file_folder(lib, llm, cls, title, summary)
    roll = summarise_folder(llm)
    meta = {"title": title, "summary": summary, "keywords": keywords, "questions": [],
            "source": os.path.basename(filename), "rows": len(rows), "columns": header, **cls}
    name = lib.unique(folder, safe_name(title))
    sub = f"{folder}/{name}".lstrip("/")
    lib.write_index(sub, title, summary, [])
    paths = []
    for i, text in enumerate(tables.markdown_sections(title, header, rows)):
        part = rows[i * tables.ROWS_PER_SECTION:(i + 1) * tables.ROWS_PER_SECTION]
        first, last = i * tables.ROWS_PER_SECTION + 1, i * tables.ROWS_PER_SECTION + len(part)
        who = sorted({r[rl["party"]].strip() for r in part if rl["party"] is not None and rl["party"] < len(r)} - {""})
        refs = [r[rl["ref"]].strip() for r in part if rl["ref"] is not None and rl["ref"] < len(r)]
        s_sum = (f"Rows {first}-{last}" + (f", {refs[0]} to {refs[-1]}" if refs else "")
                 + (f": {', '.join(who[:5])}{' and more' if len(who) > 5 else ''}" if who else ""))
        s_name = f"Rows {first:05d}-{last:05d}.md"
        lib.write_doc(sub, s_name, {**meta, "title": f"{title}, rows {first}-{last}", "summary": s_sum,
                                     "first_row": first}, text)
        lib.put_entry(sub, Entry(s_name, "doc", s_sum, top_keywords(text, 10)))
        paths.append(f"{sub}/{s_name}")
    # more than MAX_CHILDREN sections: regroup the section docs is left to self-organisation; searching
    # still works because wide folders are scored in batches
    lib.put_entry(folder, Entry(name, "folder", summary, keywords))
    lib.refresh_up(folder, roll)
    rows_facts = tables.facts_from_rows(lambda n: paths[(n - 1) // tables.ROWS_PER_SECTION], title, header, rows, cls)
    facts.FactStore(lib.root).replace_doc(sub, rows_facts)
    return {"name": os.path.basename(filename), "path": sub, "summary": summary}


def add_file(lib, llm, filename, data):
    if filename.lower().endswith(tables.TABULAR):
        return add_table(lib, llm, filename, data)
    title, md = to_markdown(filename, data)
    if not md.strip():
        raise ValueError("no text found in this file")
    summary, keywords, questions = describe(llm, title, md)
    cls = facts.classify(llm, title, md)
    folder = file_folder(lib, llm, cls, title, summary)
    roll = summarise_folder(llm)
    meta = {"title": title, "summary": summary, "keywords": keywords, "questions": questions,
            "source": os.path.basename(filename), **cls}

    if len(md) <= LONG:
        name = lib.unique(folder, safe_name(title, ".md"))
        lib.write_doc(folder, name, meta, md)
        lib.put_entry(folder, Entry(name, "doc", summary, keywords))
        path = f"{folder}/{name}".lstrip("/")
    else:
        # A long document becomes its own small subtree: one doc per section, and one folder per
        # MAX_CHILDREN sections when there are more, so every index stays small.
        name = lib.unique(folder, safe_name(title))
        sub = f"{folder}/{name}".lstrip("/")
        lib.write_index(sub, title, summary, [])
        place_sections(lib, llm, roll, sub, title, meta, summary, keywords, split_sections(md), 0)
        lib.put_entry(folder, Entry(name, "folder", summary, keywords))
        lib.refresh_up(sub, roll)
        path = sub
    lib.refresh_up(folder, roll)
    store_facts(lib, llm, path, cls)
    return {"name": os.path.basename(filename), "path": path, "summary": summary}
