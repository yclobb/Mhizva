"""Model access. BitNet runs inside llama-server (from bitnet.cpp); we talk to it over HTTP.

Two calls cover everything the engine needs:
  pick(prompt, labels)  -> probability per label, read from ONE next-token pass (no text generated)
  write(prompt, n)      -> a short generated text
FakeLLM implements the same interface with keyword overlap so the engine can be tested without a model.
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BITNET = os.path.join(ROOT, "BitNet")
MODEL = os.path.join(BITNET, "models", "BitNet-b1.58-2B-4T", "ggml-model-i2_s.gguf")
PORT = 8766

# BitNet b1.58 2B4T chat format
def chat(system, user):
    return f"System: {system}<|eot_id|>User: {user}<|eot_id|>Assistant: "


def find_server_exe():
    for sub in ("build/bin/Release", "build/bin", "build/Release/bin"):
        p = os.path.join(BITNET, sub, "llama-server.exe")
        if os.path.exists(p):
            return p
    return None


class BitNetLLM:
    def __init__(self, threads=None, ctx=4096):
        self.lock = threading.Lock()  # llama-server has one slot; keep calls in order
        self.threads = threads or max(1, (os.cpu_count() or 4) // 2)
        self.ctx = ctx
        self.proc = None
        self.state = "loading"

    def start(self):
        exe = find_server_exe()
        if not exe:
            self.state = "error"
            raise RuntimeError("llama-server.exe not found; build bitnet.cpp first")
        self.proc = subprocess.Popen(
            [exe, "-m", MODEL, "-c", str(self.ctx), "-t", str(self.threads),
             "--port", str(PORT), "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(240):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if r.status == 200 and b"ok" in r.read():
                        self.state = "ready"
                        return
            except Exception:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        self.state = "error"
        raise RuntimeError("llama-server did not become ready")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _post(self, body):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.lock, urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())

    def pick(self, prompt, labels):
        """Probability of each label as the next token, normalised over the labels (optimisation 1)."""
        out = self._post({"prompt": prompt, "n_predict": 1, "n_probs": 40,
                          "temperature": 0, "cache_prompt": True})
        probs = {}
        for step in out.get("completion_probabilities", [])[:1]:
            cands = step.get("probs") or step.get("top_probs") or step.get("top_logprobs") or []
            for c in cands:
                tok = (c.get("tok_str") or c.get("token") or "").strip()
                p = c.get("prob")
                if p is None and "logprob" in c:
                    import math
                    p = math.exp(c["logprob"])
                if tok in labels and p is not None:
                    probs[tok] = probs.get(tok, 0.0) + p
        total = sum(probs.values())
        if total <= 0:
            return {l: 1.0 / len(labels) for l in labels}
        return {l: probs.get(l, 0.0) / total for l in labels}

    def write(self, prompt, n=80, stop=None):
        out = self._post({"prompt": prompt, "n_predict": n, "temperature": 0,
                          "cache_prompt": True, "stop": stop or ["<|eot_id|>", "\n\n"]})
        return out.get("content", "").strip()


WORD = re.compile(r"[a-z0-9$]+")
STOP = set("""a an the and or of to in on for with by at from is are was were be been it this that these those
what which who whom how when where why do does did i you we they he she my our your their me us them as
about into than then there here not no yes can could would should will shall may might has have had""".split())


def stem(w):
    """Crude but consistent: invoice/invoices, renew/renews/renewed, owe/owed all meet."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    for suf in ("ing", "ed", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf) and not w.endswith("ss"):
            w = w[:-len(suf)]
            break
    return w[:-1] if len(w) > 3 and w.endswith("e") else w


CJK = re.compile(r"[一-鿿]+")
CN_SPLIT = re.compile(r"[的了是在和与及或吗呢吧请给把被]")
CN_STOP = set("的 了 是 在 和 与 及 或 有 我 我们 你 你们 他 她 它 这 那 这个 那个 吗 呢 吧 什么 怎么 哪些 哪个 一个 请 给 把 被".split())


def cjk_terms(text):
    """Chinese has no spaces: match on overlapping character pairs (合同 金额 -> 合同, 同金, 金额)."""
    out = []
    for run in CJK.findall(text):
        for part in CN_SPLIT.split(run):  # cut at particles so 合同的金额 doesn't yield 同的 / 的金
            if part:
                out += [part] if len(part) == 1 else [part[i:i + 2] for i in range(len(part) - 1)]
    return [t for t in out if t not in CN_STOP]


def has_cjk(text):
    return bool(CJK.search(text))


def plain_words(text):
    """Readable keywords (no stemming), for index lines and names. Chinese becomes character pairs."""
    return [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1] + cjk_terms(text)


def words(text):
    """Matching form: stemmed, so renew / renews / renewed meet (Chinese pairs pass through)."""
    return [stem(w) if w.isascii() else w for w in plain_words(text)]


class FakeLLM:
    """Keyword stand-in for tests: same interface, no model."""
    state = "ready"

    def start(self):
        pass

    def stop(self):
        pass

    def pick(self, prompt, labels):
        if "Question:" in prompt:
            q = set(words(prompt.split("Question:")[-1].split("\n")[0]))
        else:
            q = set(words(prompt.split("Document:")[-1].split("\n")[0]))
        scores = {}
        for l in labels:
            m = re.search(rf"^{l}\) (.*)$", prompt, re.M)
            line = m.group(1) if m else ""
            # "make a new folder" wins unless a folder shares at least two words with the document
            scores[l] = 1.5 if "new folder" in line else len(q & set(words(line))) + 0.05
        total = sum(scores.values())
        return {l: s / total for l, s in scores.items()}

    def write(self, prompt, n=80, stop=None):
        body = prompt.split("User:")[-1].split("<|eot_id|>")[0]
        if "main other party" in prompt.lower():
            m = re.search(r"(?:Supplier|Customer|Insurer):\s*([^\n.]+)", body)
            return m.group(1).strip() if m else "none"
        if "folder name" in prompt.lower():
            ws = plain_words(body.split("Document:")[-1])
            return " ".join(w.capitalize() for w in ws[:2]) or "General"
        if "Quotes:" in prompt:
            quotes = body.split("Quotes:")[-1].strip()
            return quotes.split("\n")[0][:200] if quotes else "NOT_FOUND"
        text = body.split("Document:")[-1].strip()
        first = re.split(r"(?<=[.!?])\s", text, 1)[0]
        return first[:160]
