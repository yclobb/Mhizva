"""Model access. Local models (Qwen GGUF files in models/) run inside llama-server from the official
llama.cpp build in runtime/; we talk to it over HTTP.

Two calls cover everything the engine needs:
  pick(prompt, labels)  -> probability per label, read from ONE next-token pass (no text generated)
  write(prompt, n)      -> a short generated text
Prompts are built with chat(system, user) and formatted by each model's own chat template.
TeamLLM pairs a small fast model (picks) with a larger one (writing, and picks the small one is unsure of).
FakeLLM implements the same interface with keyword overlap so the engine can be tested without a model.
"""
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, "models")

# name -> (gguf path, chat template)
CATALOGUE = {
    "qwen2.5-0.5b": (os.path.join(MODELS, "qwen2.5-0.5b-instruct-q8_0.gguf"), "chatml"),
    "qwen2.5-1.5b": (os.path.join(MODELS, "qwen2.5-1.5b-instruct-q4_k_m.gguf"), "chatml"),
    "qwen3-4b": (os.path.join(MODELS, "Qwen3-4B-Q4_K_M.gguf"), "qwen3"),
}

TEMPLATES = {
    "chatml": ("<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n",
               "<|im_end|>"),
    # Qwen3 thinks out loud unless the assistant turn starts with an empty think block
    "qwen3": ("<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"
              "<think>\n\n</think>\n\n", "<|im_end|>"),
}


@dataclass(frozen=True)
class Chat:
    system: str
    user: str
    turns: tuple = ()  # earlier (role, text) pairs, role "user" | "assistant"

    def __str__(self):  # readable form, used by FakeLLM and in logs
        past = "".join(f"{'User' if r == 'user' else 'Assistant'}: {t}<|eot_id|>" for r, t in self.turns)
        return f"System: {self.system}<|eot_id|>{past}User: {self.user}<|eot_id|>Assistant: "


def chat(system, user, turns=()):
    return Chat(system, user, tuple(turns))


def find_server_exe():
    """Official llama.cpp build in runtime/ (picks the right CPU variant itself)."""
    p = os.path.join(ROOT, "runtime", "llama-server.exe")
    return p if os.path.exists(p) else None


class ServerLLM:
    """One model in its own llama-server process."""

    def __init__(self, name="qwen2.5-1.5b", port=8766, threads=None, ctx=4096):
        self.name, self.port, self.ctx = name, port, ctx
        self.path, template = CATALOGUE[name]
        self.template, self.end = TEMPLATES[template]
        self.lock = threading.Lock()  # one request at a time per server
        # 4 beat 8 on an i7-1260P (4 performance + 8 efficiency cores); per-machine tuning comes later
        self.threads = threads or min(4, os.cpu_count() or 4)
        self.proc = None
        self.state = "loading"

    def start(self):
        exe = find_server_exe()
        if not exe or not os.path.exists(self.path):
            self.state = "error"
            raise RuntimeError(f"llama-server or model missing for {self.name}")
        self.proc = subprocess.Popen(
            [exe, "-m", self.path, "-c", str(self.ctx), "-t", str(self.threads), "-np", "1",
             "--port", str(self.port), "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(240):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if r.status == 200 and b"ok" in r.read():
                        self.state = "ready"
                        return
            except Exception:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        self.state = "error"
        raise RuntimeError(f"llama-server for {self.name} did not become ready")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def format(self, prompt):
        """The model's own chat template, earlier turns included as real turns."""
        if not isinstance(prompt, Chat):
            return str(prompt)
        text = self.template.format(system=prompt.system, user=prompt.user)
        if prompt.turns and "<|im_start|>" in self.template:
            past = "".join(f"<|im_start|>{r}\n{t}<|im_end|>\n" for r, t in prompt.turns)
            text = text.replace("<|im_start|>user\n", past + "<|im_start|>user\n", 1)
        return text

    def _post(self, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/completion",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.lock, urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())

    def pick(self, prompt, labels):
        """Probability of each label as the next token, normalised over the labels (optimisation 1)."""
        out = self._post({"prompt": self.format(prompt), "n_predict": 1, "n_probs": 40,
                          "temperature": 0, "cache_prompt": True})
        probs = {}
        for step in out.get("completion_probabilities", [])[:1]:
            cands = step.get("probs") or step.get("top_probs") or step.get("top_logprobs") or []
            for c in cands:
                tok = (c.get("tok_str") or c.get("token") or "").strip().rstrip(")")
                p = c.get("prob")
                if p is None and "logprob" in c:
                    p = math.exp(c["logprob"])
                if tok in labels and p is not None:
                    probs[tok] = probs.get(tok, 0.0) + p
        total = sum(probs.values())
        if total <= 0:
            return {l: 1.0 / len(labels) for l in labels}
        return {l: probs.get(l, 0.0) / total for l in labels}

    def form(self, prompt, schema, n=120):
        """Fill a JSON form: llama-server constrains decoding to the schema, so the output is always valid."""
        out = self._post({"prompt": self.format(prompt), "n_predict": n, "temperature": 0,
                          "cache_prompt": True, "json_schema": schema})
        return json.loads(out.get("content") or "null")

    def write(self, prompt, n=80, stop=None):
        stop = [self.end if s == "<|eot_id|>" else s for s in (stop or ["<|eot_id|>", "\n\n"])]
        out = self._post({"prompt": self.format(prompt), "n_predict": n, "temperature": 0,
                          "cache_prompt": True, "stop": list(dict.fromkeys(stop + [self.end]))})
        return out.get("content", "").strip()


class TeamLLM:
    """Lucas's nested team: the small model makes the many quick picks; when its top two choices are
    within ESCALATE of each other the large model decides instead; the large model does all writing."""
    ESCALATE = 0.2

    def __init__(self, small, large):
        self.small, self.large = small, large
        self.escalations = 0

    @property
    def state(self):
        states = {self.small.state, self.large.state}
        return "error" if "error" in states else "loading" if "loading" in states else "ready"

    def start(self):
        threads = [threading.Thread(target=m.start) for m in (self.small, self.large)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def stop(self):
        self.small.stop()
        self.large.stop()

    def pick(self, prompt, labels):
        probs = self.small.pick(prompt, labels)
        top = sorted(probs.values(), reverse=True)
        if len(top) > 1 and top[0] - top[1] < self.ESCALATE:
            self.escalations += 1
            return self.large.pick(prompt, labels)
        return probs

    def write(self, prompt, n=80, stop=None):
        return self.large.write(prompt, n, stop)

    def form(self, prompt, schema, n=120):
        """Understanding runs on the small model (fast); the large one is not needed to fill a form."""
        return self.small.form(prompt, schema, n)


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
        prompt = str(prompt)
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
        prompt = str(prompt)
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
