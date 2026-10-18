"""mdtree local server: serves the UI and the JSON API on http://127.0.0.1:8765.

Run:  python -I server.py            (BitNet; starts llama-server itself)
      python -I server.py --fake     (keyword stand-in model, for testing without BitNet)
"""
import email.parser
import email.policy
import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import facts  # noqa: E402
from engine.ingest import add_file  # noqa: E402
from engine.llm import BitNetLLM, FakeLLM  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

PORT = 8765
UI = os.path.join(HERE, "ui")
FAKE = "--fake" in sys.argv
lib = Library(os.environ.get("MDTREE_LIBRARY", os.path.join(HERE, "library")))
llm = FakeLLM() if FAKE else BitNetLLM()
searcher = Searcher(lib, llm)
work = threading.Lock()  # one upload or question at a time; the model is single-slot anyway
busy = {"n": 0}
answers = {}  # answer id -> response, for spreadsheet export


def parse_multipart(content_type, body):
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    for part in msg.iter_parts():
        name = part.get_filename()
        if name:
            yield os.path.basename(name), part.get_payload(decode=True) or b""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/status":
                return self.send(200, {"model": llm.state, "docs": lib.count_docs(), "busy": busy["n"] > 0})
            if u.path == "/api/tree":
                return self.send(200, lib.tree())
            if u.path == "/api/export":
                qs = parse_qs(u.query)
                ans = answers.get(qs.get("id", [""])[0])
                fmt = qs.get("format", ["xlsx"])[0]
                if not ans or not ans.get("table") or fmt not in ("xlsx", "csv"):
                    return self.send(404, {"error": "nothing to export"})
                data = facts.to_xlsx(ans["table"]) if fmt == "xlsx" else facts.to_csv(ans["table"])
                ctype = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx"
                         else "text/csv; charset=utf-8")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", f'attachment; filename="Mhizva.{fmt}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if u.path == "/api/doc":
                path = parse_qs(u.query).get("path", [""])[0]
                meta, md = lib.read_doc(path)
                return self.send(200, {"path": path, "title": meta.get("title", path), "markdown": md})
        except (FileNotFoundError, ValueError):
            return self.send(404, {"error": "not found"})
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = os.path.abspath(os.path.join(UI, rel))
        if f.startswith(UI + os.sep) and os.path.isfile(f):
            with open(f, "rb") as fh:
                return self.send(200, raw=fh.read(),
                                 ctype=mimetypes.guess_type(f)[0] or "application/octet-stream")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if llm.state != "ready":
            return self.send(503, {"error": "The model is still loading."})
        busy["n"] += 1
        try:
            with work:
                if u.path == "/api/ask":
                    q = (json.loads(body or b"{}").get("question") or "").strip()
                    if not q:
                        return self.send(400, {"error": "empty question"})
                    res = searcher.ask(q)
                    answers[res["id"]] = res  # kept for /api/export
                    while len(answers) > 200:
                        answers.pop(next(iter(answers)))
                    return self.send(200, res)
                if u.path == "/api/upload":
                    filed, errors = [], []
                    for name, data in parse_multipart(self.headers.get("Content-Type", ""), body):
                        try:
                            filed.append(add_file(lib, llm, name, data))
                        except Exception as e:  # report per file, keep going
                            errors.append({"name": name, "error": str(e)})
                    searcher.forget()
                    return self.send(200, {"filed": filed, "errors": errors})
            self.send(404, {"error": "not found"})
        finally:
            busy["n"] -= 1


def main():
    threading.Thread(target=llm.start, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"mdtree on http://127.0.0.1:{PORT} ({'fake model' if FAKE else 'BitNet'})", flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    try:
        srv.serve_forever()
    finally:
        llm.stop()


if __name__ == "__main__":
    main()
