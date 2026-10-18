"""Generate B1 (Harborline Electrical) spreadsheets to the spec's exact constraints, then verify them.

  payroll.csv      3,140 rows: one row per pay component per fortnightly payslip, 5 employees, 104 fortnights
                   (Friday period-ends 2022-11-04 .. 2026-10-16). Super is shown on the base row AND on its own
                   'super' row (a deliberate double-count trap; the super row's amount column is blank).
  bank-q1fy27.csv  1,240 rows, 1 Jul - 30 Sep 2026, reconciling running balance, noisy descriptions.

Seeded, so the same files come out every time. Run: python -I -X utf8 training/businesses/gen_b1.py
"""
import csv
import datetime as dt
import os
import random
from decimal import ROUND_HALF_UP, Decimal

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "B1")
C = Decimal("0.01")
R = random.Random(20261018)


def d(x):
    return Decimal(str(x)).quantize(C, ROUND_HALF_UP)


# ------------------------------------------------------------------ payroll
EMPLOYEES = [("HAR-02", "Ryan Petrie", "electrician", Decimal("52.40")),
             ("HAR-03", "Amir Osman", "electrician", Decimal("50.10")),
             ("HAR-04", "Linh Nguyen", "electrician", Decimal("49.80")),
             ("HAR-05", "Jake Webb", "electrician", Decimal("48.60")),
             ("HAR-06", "Tom Brennan", "apprentice", Decimal("21.20"))]
EXTRAS = ["overtime_1_5", "overtime_2_0", "travel_allowance", "tool_allowance", "bonus"]
FY26 = (dt.date(2025, 7, 1), dt.date(2026, 6, 30))
Q1 = (dt.date(2026, 7, 1), dt.date(2026, 9, 30))


def inside(day, win):
    return win[0] <= day <= win[1]


def payroll():
    # 104 fortnights ending 2026-10-16 start on 2022-11-04 (the spec's "2024-10-25 .. 2026-10-16" is only 52
    # fortnights, which cannot hold 3,140 rows at 5-7 components per payslip)
    ends = [dt.date(2022, 11, 4) + dt.timedelta(days=14 * i) for i in range(104)]
    assert ends[-1] == dt.date(2026, 10, 16)
    # component counts: x5 + x6 + x7 = 520 payslips, 5*x5 + 6*x6 + 7*x7 = 3,140 rows
    sizes = [5] * 100 + [6] * 300 + [7] * 120
    R.shuffle(sizes)
    slips = []
    for pi, end in enumerate(ends):
        for ei, (eid, name, role, rate) in enumerate(EMPLOYEES):
            n = sizes[pi * 5 + ei]
            extras = R.sample(EXTRAS, n - 2)  # base + super + extras
            comps = {"base": (Decimal("76.00"), rate)}
            for x in extras:
                if x.startswith("overtime"):
                    mult = Decimal("1.5") if x == "overtime_1_5" else Decimal("2.0")
                    comps[x] = (d(R.choice([2, 3, 4, 5, 6, 8])), (rate * mult).quantize(C))
                else:
                    comps[x] = (None, None)
            amounts = {}
            for k, (h, r) in comps.items():
                if h is not None:
                    amounts[k] = (h * r).quantize(C)
                elif k == "travel_allowance":
                    amounts[k] = d(R.choice([45, 60, 75, 90]))
                elif k == "tool_allowance":
                    amounts[k] = d(R.choice([18.5, 22, 27.5]))
                else:
                    amounts[k] = d(R.choice([150, 250, 400]))
            slips.append({"end": end, "emp": EMPLOYEES[ei], "comps": comps, "amt": amounts})

    def gross(s):
        return sum(s["amt"].values())

    def scale(group, target):
        """Scale the hour-based components of a group of payslips so their gross hits target exactly;
        the last cent of difference goes on a payslip's allowance/bonus row (no hours to keep consistent)."""
        cur = sum(gross(s) for s in group)
        fixed = sum(v for s in group for k, v in s["amt"].items() if s["comps"][k][0] is None)
        f = (target - fixed) / (cur - fixed)
        for s in group:
            for k, (h, r) in list(s["comps"].items()):
                if h is not None:
                    nh = (h * f).quantize(C)
                    s["comps"][k] = (nh, r)
                    s["amt"][k] = (nh * r).quantize(C)
        diff = target - sum(gross(s) for s in group)
        holder = next(s for s in reversed(group) if any(s["comps"][k][0] is None for k in s["amt"]))
        k = next(k for k in holder["amt"] if holder["comps"][k][0] is None)
        holder["amt"][k] += diff
        assert sum(gross(s) for s in group) == target

    appr26 = [s for s in slips if s["emp"][0] == "HAR-06" and inside(s["end"], FY26)]
    rest26 = [s for s in slips if s["emp"][0] != "HAR-06" and inside(s["end"], FY26)]
    q1 = [s for s in slips if inside(s["end"], Q1)]
    scale(appr26, Decimal("41800.00"))
    scale(rest26, Decimal("418600.00") - Decimal("41800.00"))
    scale(q1, Decimal("107000.00"))

    # super guarantee rates by financial year: FY23 10.5%, FY24 11%, FY25 11.5%, FY26 onwards 12%
    def sg(day):
        for limit, rate in ((dt.date(2023, 6, 30), "0.1050"), (dt.date(2024, 6, 30), "0.1100"),
                            (dt.date(2025, 6, 30), "0.1150")):
            if day <= limit:
                return Decimal(rate)
        return Decimal("0.1200")
    for s in slips:
        s["rate"] = sg(s["end"])
        s["super"] = (gross(s) * s["rate"]).quantize(C, ROUND_HALF_UP)
    for win, target in ((FY26, Decimal("50232.00")), (Q1, Decimal("12840.00"))):
        group = [s for s in slips if inside(s["end"], win)]
        group[-1]["super"] += target - sum(s["super"] for s in group)

    rows = []
    for s in slips:
        eid, name, role, _ = s["emp"]
        for k, (h, r) in s["comps"].items():
            rows.append([s["end"].isoformat(), eid, name, role, k, f"{h:.2f}" if h is not None else "",
                         f"{r:.2f}" if r is not None else "", f"{s['amt'][k]:.2f}",
                         f"{s['rate']:.4f}" if k == "base" else "", f"{s['super']:.2f}" if k == "base" else ""])
        rows.append([s["end"].isoformat(), eid, name, role, "super", "", "", "", f"{s['rate']:.4f}", f"{s['super']:.2f}"])
    return rows, slips


# ------------------------------------------------------------------ bank
def bank(slips):
    start, end = Q1
    days = [start + dt.timedelta(n) for n in range((end - start).days + 1)]
    debits, credits = [], []  # (date, description, amount>0, category, party_ref)

    # wages: one transfer per Q1 pay period, equal to that fortnight's gross (sums to $107,000)
    by_end = {}
    for s in slips:
        if inside(s["end"], Q1):
            by_end[s["end"]] = by_end.get(s["end"], Decimal(0)) + sum(s["amt"].values())
    for e, amt in sorted(by_end.items()):
        debits.append((e, "TRANSFER TO PAYROLL ACCT", amt, "wages", ""))
    # rent: 3 x $5,335.00 on the 1st (spec value; see note about the lease escalation)
    for m in (7, 8, 9):
        debits.append((dt.date(2026, m, 1), "HALLORAN STREET HOLDINGS RENT", Decimal("5335.00"), "rent", "CON-03"))
    # bills from the register paid in Q1
    for day, desc, amt, cat, ref in [(dt.date(2026, 7, 8), "BPAY TELSTRA BUSINESS", "495.00", "utilities", "BILL-2215"),
                                     (dt.date(2026, 7, 28), "APEX TOOLS PTY LTD", "1650.00", "materials", "BILL-2213"),
                                     (dt.date(2026, 8, 25), "SAFEWORK SUPPLIES", "374.00", "materials", "BILL-2214"),
                                     (dt.date(2026, 8, 26), "BPAY ONESTEEL", "2783.00", "materials", "BILL-2211")]:
        debits.append((day, desc, Decimal(amt), cat, ref))

    def spread(total, n, cat, names, ref_fn=lambda: ""):
        """n debits in category `cat` summing exactly to `total`."""
        w = [R.uniform(0.4, 1.6) for _ in range(n)]
        amts = [(total * Decimal(str(x / sum(w)))).quantize(C) for x in w]
        amts[-1] += total - sum(amts)
        for a in amts:
            debits.append((R.choice(days), R.choice(names), a, cat, ref_fn()))

    fixed_mat = sum(a for _, _, a, c, _ in debits if c == "materials")
    spread(Decimal("8920.00"), 190, "fuel", ["AMPOL BERESFIELD", "BP THORNTON", "7-ELEVEN 2322", "SHELL COLES EXPRESS"])
    spread(Decimal("52180.00") - fixed_mat, 330, "materials",
           ["VANTAGE ELEC 91026", "vantage electrical supp", "ONESTEEL NEWCASTLE", "BUNNINGS 6417",
            "REXEL CARDIFF", "APEX TOOLS PTY LTD"], lambda: R.choice(["", "", "", f"PO{R.randint(4400, 4499)}"]))
    used = sum(a for _, _, a, _, _ in debits)
    remaining = Decimal("226075.00") - used
    others = [("vehicle", ["NRMA INSURANCE", "TOYOTA FINANCE", "LINKT TOLLS", "BRIDGESTONE SELECT"]),
              ("software", ["XERO AU", "SERVICEM8", "MICROSOFT 365", "GOOGLE WORKSPACE"]),
              ("utilities", ["AGL ENERGY", "TELSTRA", "HUNTER WATER"]),
              ("subcontractors", ["HUNTER DATA CABLING", "SPARKY HIRE PTY LTD", "COASTAL TRENCHING"]),
              ("insurance", ["SOUTHERN CROSS UW", "ICARE WORKERS COMP"]),
              ("other", ["BANK FEE", "MERCHANT FEE", "NSW FAIR TRADING LICENCE", "TAFE NSW"])]
    shares = [0.17, 0.05, 0.08, 0.5, 0.12, 0.08]
    counts = [60, 30, 30, 40, 10, 34]
    # credits count chosen so the file has exactly 1,240 rows
    n_debit = len(debits) + sum(counts)
    n_credit = 1240 - n_debit
    parts = [(remaining * Decimal(str(s))).quantize(C) for s in shares]
    parts[-1] += remaining - sum(parts)
    for (cat, names), part, n in zip(others, parts, counts):
        spread(part, n, cat, names)

    # credits: known invoice payments, then other customer receipts not in the 18-file sample
    known = [(dt.date(2026, 7, 21), "MERIDIAN FREIGHT PTY LTD", Decimal("7150.00"), "INV-1043"),
             (dt.date(2026, 8, 27), "HALLORAN ST UNITS STRATA", Decimal("1419.00"), "INV-1049"),
             (dt.date(2026, 9, 15), "COASTAL PROPERTY GROUP", Decimal("4000.00"), "INV-1044")]
    for day, desc, amt, ref in known:
        credits.append((day, desc, amt, "", ref))
    rest = Decimal("284650.00") - sum(a for _, _, a, _, _ in credits)
    n = n_credit - len(credits)
    w = [R.uniform(0.3, 1.7) for _ in range(n)]
    amts = [(rest * Decimal(str(x / sum(w)))).quantize(C) for x in w]
    amts[-1] += rest - sum(amts)
    payers = ["KESTREL FABRICATION", "APEX MINING LTD", "MERIDIAN FREIGHT PTY LTD", "COASTAL PROPERTY GROUP",
              "BONDI RESTAURANT CO", "WHITFIELD MEDICAL", "STRIPE PAYOUT", "SQUARE AU PAYOUT", "CASH DEPOSIT"]
    for a in amts:
        credits.append((R.choice(days), R.choice(payers), a, "", R.choice(["", "", f"INV-{R.randint(1051, 1099)}"])))

    # merge, order by date (stable), running balance
    events = [(x[0], 1, x) for x in credits] + [(x[0], 0, x) for x in debits]
    events.sort(key=lambda e: (e[0], R.random()))
    bal = Decimal("38240.75")
    rows = []
    for day, is_credit, (_, desc, amt, cat, ref) in events:
        signed = amt if is_credit else -amt
        bal += signed
        rows.append([day.strftime("%d/%m/%Y"), desc, f"{signed:.2f}", "CREDIT" if is_credit else "DEBIT",
                     f"{bal:.2f}", "" if is_credit else cat, ref])
    return rows


def write(name, header, rows):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def verify():
    """Re-read the files and check every hard constraint from the spec."""
    def rd(name):
        return list(csv.DictReader(open(os.path.join(OUT, name), encoding="utf-8")))
    p = rd("payroll.csv")
    D = Decimal
    gross = lambda rows: sum(D(r["amount"]) for r in rows if r["component"] != "super")
    sup = lambda rows: sum(D(r["super_amount"]) for r in rows if r["component"] == "super")
    fy26 = [r for r in p if "2025-07-01" <= r["pay_period_end"] <= "2026-06-30"]
    q1 = [r for r in p if "2026-07-01" <= r["pay_period_end"] <= "2026-09-30"]
    checks = {
        "payroll rows = 3,140": len(p) == 3140,
        "FY2026 gross = 418,600.00": gross(fy26) == D("418600.00"),
        "FY2026 super = 50,232.00": sup(fy26) == D("50232.00"),
        "Q1 FY27 gross = 107,000.00": gross(q1) == D("107000.00"),
        "Q1 FY27 super = 12,840.00": sup(q1) == D("12840.00"),
        "apprentice FY2026 gross = 41,800.00": gross([r for r in fy26 if r["employee_id"] == "HAR-06"]) == D("41800.00"),
        "components per payslip 5-7": all(5 <= n <= 7 for n in __import__("collections").Counter(
            (r["pay_period_end"], r["employee_id"]) for r in p).values()),
    }
    b = rd("bank-q1fy27.csv")
    cr = sum(D(r["amount"]) for r in b if r["type"] == "CREDIT")
    db = -sum(D(r["amount"]) for r in b if r["type"] == "DEBIT")
    cat = lambda c: -sum(D(r["amount"]) for r in b if r["category"] == c)
    bal, ok = D("38240.75"), True
    for r in b:
        bal += D(r["amount"])
        ok &= bal == D(r["balance"])
    checks.update({
        "bank rows = 1,240": len(b) == 1240,
        "total CREDIT = 284,650.00": cr == D("284650.00"),
        "total DEBIT = 226,075.00": db == D("226075.00"),
        "closing balance = 96,815.75": D(b[-1]["balance"]) == D("96815.75"),
        "balance reconciles row by row": ok,
        "fuel = 8,920.00": cat("fuel") == D("8920.00"),
        "materials = 52,180.00": cat("materials") == D("52180.00"),
        "rent = 16,005.00": cat("rent") == D("16005.00"),
        "wages = 107,000.00": cat("wages") == D("107000.00"),
        "category only on DEBIT rows": all((r["category"] == "") == (r["type"] == "CREDIT") for r in b),
        "dates within Q1 FY27": all("2026-07-01" <= dt.datetime.strptime(r["date"], "%d/%m/%Y").date().isoformat()
                                    <= "2026-09-30" for r in b),
    })
    for k, v in checks.items():
        print("PASS" if v else "FAIL", k)
    return all(checks.values())


if __name__ == "__main__":
    prows, slips = payroll()
    write("payroll.csv", ["pay_period_end", "employee_id", "employee_name", "role", "component", "hours", "rate",
                          "amount", "super_rate", "super_amount"], prows)
    write("bank-q1fy27.csv", ["date", "description", "amount", "type", "balance", "category", "party_ref"], bank(slips))
    raise SystemExit(0 if verify() else 1)
