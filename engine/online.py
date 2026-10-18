"""Online mode: look a general question up on the web (opt-in, off by default).

Only the question text leaves the computer, never documents or facts. Results are returned separately
(title, link, short extract) and are never filed, cached into the library or mixed into totals.
Source: Wikipedia's public API (free, no account), in the question's language (English or Chinese).
"""
import json
import re
import urllib.parse
import urllib.request

UA = "Mhizva/0.1 (local document assistant; online mode)"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def search(question, zh=False, limit=3):
    """[{title, url, text}] for the question, best matches first. Empty list if offline or nothing found."""
    host = "zh.wikipedia.org" if zh else "en.wikipedia.org"
    q = re.sub(r"[?？!！]+", " ", question).strip()
    try:
        hits = _get(f"https://{host}/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": q, "srlimit": limit, "format": "json"}))
        ids = [str(h["pageid"]) for h in hits.get("query", {}).get("search", [])]
        if not ids:
            return []
        pages = _get(f"https://{host}/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "prop": "extracts|info", "exintro": 1, "explaintext": 1, "inprop": "url",
             "pageids": "|".join(ids), "format": "json"}))["query"]["pages"]
    except Exception:
        return []
    out = []
    for pid in ids:
        p = pages.get(pid, {})
        text = re.sub(r"\s+", " ", p.get("extract", "")).strip()
        if text:
            out.append({"title": p.get("title", ""), "url": p.get("fullurl", ""), "text": text[:900]})
    return out
