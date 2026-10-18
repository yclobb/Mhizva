"""Score the understanding step on tests/intent_set.jsonl: intent accuracy (+ direction / group_by where labelled).

Run: python -I -X utf8 tests/eval_intent.py rules            (keyword baseline, no model)
     python -I -X utf8 tests/eval_intent.py form:qwen2.5-0.5b              (model-filled JSON form)
     python -I -X utf8 tests/eval_intent.py pick:qwen2.5-0.5b              (one-letter pick)
     python -I -X utf8 tests/eval_intent.py pick:qwen2.5-0.5b,qwen2.5-1.5b (0.5B picks, 1.5B takes close calls)
     python -I -X utf8 tests/eval_intent.py pickrules:qwen2.5-0.5b,qwen2.5-1.5b (same + keyword rules as prior)
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from engine import intent  # noqa: E402
from engine.llm import ServerLLM, TeamLLM  # noqa: E402


def main(who):
    cases = [json.loads(l) for l in open(os.path.join(HERE, "intent_set.jsonl"), encoding="utf-8") if l.strip()]
    llm, how = None, who.split(":")[0]
    setup = who.split(":")[-1]
    deep = None
    if how == "cascade":  # cascade:<fast>,<deep>: rules + fast form agree -> accept; else the deep model decides
        a, b = setup.split(",")
        llm, deep = ServerLLM(a, 8796), ServerLLM(b, 8797)
        llm.start()
        deep.start()
    elif who != "rules":
        if "," in setup:
            a, b = setup.split(",")
            llm = TeamLLM(ServerLLM(a, 8796), ServerLLM(b, 8797))
        else:
            llm = ServerLLM(setup, 8796)
        llm.start()
    ok = extra_ok = extra_n = 0
    wrong, times = [], []
    try:
        for c in cases:
            t = time.time()
            if llm is None:
                got = intent.rules_intent(c["q"])
            elif how == "cascade":
                got = intent.cascade(llm, deep, c["q"])
            elif how == "form":
                got = intent.parse(llm, c["q"])
            else:  # pick / pickrules
                got = intent.understand(llm, c["q"], rules_weight=0.3 if how == "pickrules" else 0.0)
            times.append(time.time() - t)
            hit = got["intent"] == c["intent"]
            ok += hit
            if llm is not None and hasattr(llm, "escalations"):
                pass
            for k in ("direction", "group_by"):
                if k in c:
                    extra_n += 1
                    good = (got.get(k) or "").lower().startswith(c[k].lower()[:4])
                    extra_ok += good
                    if not good:
                        wrong.append(f"  {k}: {c['q']!r} want {c[k]} got {got.get(k)}")
            if not hit:
                wrong.append(f"  intent: {c['q']!r} want {c['intent']} got {got['intent']}")
    finally:
        if llm:
            llm.stop()
        if deep:
            deep.stop()
    esc = f", escalated {llm.escalations}" if llm is not None and hasattr(llm, "escalations") else ""
    esc += f", deep {intent.DEEP_CALLS[0]}" if deep else ""
    print(f"{who}{esc}: intent {ok}/{len(cases)} ({ok / len(cases):.0%}), fields {extra_ok}/{extra_n}, "
          f"avg {sum(times) / len(times):.2f}s")
    print("\n".join(wrong))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "rules")
