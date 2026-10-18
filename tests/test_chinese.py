"""Chinese documents and questions: matching, ¥/元/万 amounts, 年月日 dates, totals, trends, mixed currency.

Run: python -I -X utf8 tests/test_chinese.py   (from C:\\Users\\lucas\\mdtree)
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import facts  # noqa: E402
from engine.ingest import add_file  # noqa: E402
from engine.llm import FakeLLM, words  # noqa: E402
from engine.search import Searcher  # noqa: E402
from engine.tree import Library  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("PASS" if cond else "FAIL", name, detail)
    if not cond:
        failures.append(name)


DOCS = {
    "王先生装修合同.txt": "装修合同：甲方王先生，乙方明亮建筑公司。\n合同总价为人民币12万元。2026年3月1日开工。",
    "发票 FP-2001 李女士.txt": "发票 FP-2001，客户：李女士。\n应付金额：¥8,500。付款日期：2026年4月10日。",
    "发票 FP-2002 张先生.txt": "发票 FP-2002，客户：张先生。\n应付金额：¥3,200。付款日期：2026年4月25日。",
    "员工请假制度.md": "# 员工请假制度\n\n全职员工每年有15天带薪年假。请假需提前两周向经理申请。",
}
BILLS = [("木材公司", [("2026年1月15日", 10000), ("2026年2月12日", 11000), ("2026年3月16日", 12500), ("2026年4月14日", 14000)]),
         ("水管公司", [("2026年1月20日", 5000), ("2026年3月18日", 4800), ("2026年4月22日", 4700)])]


def main():
    check("pairs", words("合同的总金额") == ["合同", "总金", "金额"], str(words("合同的总金额")))
    check("amount 万元", [a[:2] for a in facts.find_amounts("合同总价为人民币12万元。")] == [(120000.0, "CNY")])
    check("amount ¥", [a[:2] for a in facts.find_amounts("应付金额：¥8,500。")] == [(8500.0, "CNY")])
    check("date 年月日", facts.parse_dates("付款日期：2026年4月10日。") == ["2026-04-10"])
    check("routing", [facts.question_kind(q) for q in ("发票一共多少钱？", "列出所有发票", "按客户画图表", "木材价格在上涨吗？", "年假有几天？")]
          == ["aggregate", "table", "chart", "trend", "lookup"])
    check("4月 window", facts.date_window("4月到期的发票") is not None)

    root = tempfile.mkdtemp(prefix="mhizva_zh_")
    try:
        lib, llm = Library(root), FakeLLM()
        for name, text in DOCS.items():
            add_file(lib, llm, name, text.encode("utf-8"))
        for sup, pts in BILLS:
            for i, (d, a) in enumerate(pts, 1):
                add_file(lib, llm, f"{sup}账单{i}.txt", f"木料和材料账单。\nSupplier: {sup}\n\n总计：¥{a}。日期：{d}。".encode("utf-8"))
        # one Australian-dollar invoice, so mixed currencies must not be added together
        add_file(lib, llm, "Invoice INV-9 Smith.txt", b"Tax invoice INV-9 to Smith.\n\nAmount due: $500. Due date: 10 April 2026.")
        s = Searcher(lib, llm)

        r = s.ask("发票一共要收多少钱？")
        check("zh total", r["kind"] == "aggregate" and "¥11,700" in r["answer"] and "合计" in r["answer"], r["answer"])

        r = s.ask("列出4月的所有发票")
        names = sorted(x[0] for x in r["table"]["rows"]) if r.get("table") else []
        check("zh table + window", "发票 FP-2001 李女士" in names and "发票 FP-2002 张先生" in names, str(names))
        check("zh columns", r.get("table") and r["table"]["columns"][3]["name"] == "金额")

        r = s.ask("哪些供应商在涨价？")
        rows = r["table"]["rows"] if r.get("table") else []
        check("zh per-supplier trend", r["kind"] == "trend" and rows and rows[0][0] == "木材公司"
              and r["table"]["trend_codes"][0] == "rising", r["answer"])

        r = s.ask("木材价格的走势")
        check("zh trend line", r["kind"] == "trend" and r.get("chart") and r["chart"]["type"] == "line"
              and r["chart"]["points"][0]["x"] == "2026年1月", r["answer"])

        r = s.ask("What is the total amount for April 2026?")
        cov = r.get("coverage") or {}
        check("mixed currency not added", r["kind"] == "aggregate" and cov.get("excluded_other_currency"),
              f"{r['answer']} | {cov.get('currency')} excluded {cov.get('excluded_other_currency')}")

        r = s.ask("员工年假有几天？")
        check("zh lookup", r["kind"] == "lookup" and r["found"] and "15天" in r["quotes"][0]["text"],
              r["quotes"][0]["text"] if r["quotes"] else r["answer"])
        r = s.ask("停车位在哪里？")
        check("zh not found", not r["found"] and r["answer"] == "在你的文件里没有找到这个答案。", r["answer"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(failures)} failures")
    return len(failures)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
