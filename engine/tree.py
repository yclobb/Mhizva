"""The library: a folder tree of markdown files where every folder has an index.md.

index.md (strict format, optimisation 8):
    # <folder title>
    <one-line summary of everything inside>

    - <name> | doc|folder | <summary> | <keyword, keyword, ...>

Documents carry their metadata in a first-line comment:
    <!-- mdtree {"title": ..., "summary": ..., "keywords": [...], "questions": [...], "source": ...} -->
"""
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field

INDEX = "index.md"
MAX_LINE = 160  # characters of summary per index line
META = re.compile(r"^<!-- mdtree (\{.*?\}) -->\n?", re.S)


@dataclass
class Entry:
    name: str
    kind: str  # "doc" | "folder"
    summary: str
    keywords: list = field(default_factory=list)

    def line(self):
        s = " ".join(self.summary.split())[:MAX_LINE].replace("|", "/")
        return f"- {self.name} | {self.kind} | {s} | {', '.join(self.keywords[:10])}"


def safe_name(text, ext=""):
    n = re.sub(r"[^\w\- ]+", "", text).strip()
    n = re.sub(r"\s+", " ", n)[:60] or "Untitled"
    return n + ext


class Library:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        if not os.path.exists(self._p("", INDEX)):
            self.write_index("", "Library", "Everything you have added.", [])

    def _p(self, rel, *more):
        p = os.path.abspath(os.path.join(self.root, rel.replace("/", os.sep), *more))
        if p != self.root and not p.startswith(self.root + os.sep):
            raise ValueError("path outside library")
        return p

    # ---- index files
    def read_index(self, folder):
        try:
            with open(self._p(folder, INDEX), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return os.path.basename(folder) or "Library", "", []
        title = lines[0].lstrip("# ").strip() if lines else ""
        summary = lines[1].strip() if len(lines) > 1 else ""
        entries = []
        for ln in lines[2:]:
            if not ln.startswith("- "):
                continue
            parts = [p.strip() for p in ln[2:].split(" | ")]
            if len(parts) >= 3:
                kws = [k.strip() for k in parts[3].split(",")] if len(parts) > 3 and parts[3] else []
                entries.append(Entry(parts[0], parts[1], parts[2], kws))
        return title, summary, entries

    def write_index(self, folder, title, summary, entries):
        os.makedirs(self._p(folder), exist_ok=True)
        body = [f"# {title}", " ".join(summary.split()), ""] + [e.line() for e in entries]
        with open(self._p(folder, INDEX), "w", encoding="utf-8") as f:
            f.write("\n".join(body) + "\n")

    def entries(self, folder):
        return self.read_index(folder)[2]

    def put_entry(self, folder, entry):
        title, summary, entries = self.read_index(folder)
        entries = [e for e in entries if e.name != entry.name] + [entry]
        self.write_index(folder, title, summary, entries)

    # ---- documents
    def write_doc(self, folder, name, meta, markdown):
        with open(self._p(folder, name), "w", encoding="utf-8") as f:
            f.write(f"<!-- mdtree {json.dumps(meta, ensure_ascii=False)} -->\n{markdown.strip()}\n")

    def read_doc(self, path):
        with open(self._p(path), encoding="utf-8") as f:
            text = f.read()
        m = META.match(text)
        meta = json.loads(m.group(1)) if m else {}
        return meta, text[m.end():] if m else text

    def is_folder(self, path):
        return os.path.isdir(self._p(path))

    def unique(self, folder, name):
        base, ext = os.path.splitext(name)
        n, i = name, 2
        while os.path.exists(self._p(folder, n)):
            n, i = f"{base} {i}{ext}", i + 1
        return n

    # ---- folder summaries roll up to the root
    def folder_keywords(self, folder, cap=12):
        c = Counter(k for e in self.entries(folder) for k in e.keywords)
        return [k for k, _ in c.most_common(cap)]

    def refresh_up(self, folder, summarise):
        """Rewrite the summary of `folder` and every ancestor, and their lines in the parent index."""
        while True:
            title, _, entries = self.read_index(folder)
            summary = summarise(title, entries)
            self.write_index(folder, title, summary, entries)
            if folder == "":
                return
            parent = os.path.dirname(folder.replace("\\", "/"))
            self.put_entry(parent, Entry(os.path.basename(folder), "folder", summary,
                                         self.folder_keywords(folder)))
            folder = parent

    # ---- for the UI
    def tree(self, folder=""):
        title, summary, entries = self.read_index(folder)
        node = {"name": title if folder == "" else os.path.basename(folder), "path": folder,
                "summary": summary, "kind": "folder", "children": []}
        for e in entries:
            p = f"{folder}/{e.name}".lstrip("/")
            if e.kind == "folder":
                node["children"].append(self.tree(p))
            else:
                node["children"].append({"name": e.name[:-3] if e.name.endswith(".md") else e.name,
                                         "path": p, "summary": e.summary, "kind": "doc"})
        return node

    def count_docs(self, folder=""):
        n = 0
        for e in self.entries(folder):
            n += self.count_docs(f"{folder}/{e.name}".lstrip("/")) if e.kind == "folder" else 1
        return n
