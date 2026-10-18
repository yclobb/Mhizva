"""Statistics and trend analysis for any numbers over time (sales, costs, stock prices, scores).

Everything is calculated in code from the fact rows; nothing is estimated by a model:
  level      first, last, change, mean, median, min / max (with dates), standard deviation
  movement   period-to-period returns, volatility (annualised for daily data), best / worst period
  risk       maximum drawdown: the largest fall from a peak, with its dates and whether it recovered
  trend      least-squares slope per period and how well a straight line fits (R squared)
  momentum   latest value vs its 20- and 50-period moving averages
  seasonality average by calendar month, when there are at least 12 months
"""
import math
import re
import statistics as st
from datetime import date

from .llm import words

MAX_POINTS = 240  # a chart shows at most this many points; statistics always use every point


def pick_measure(rows, question):
    """The column/label the question is about ('closing price' -> Close), else the most common label."""
    labels = {}
    for r in rows:
        if r["amount"] is not None and r["date"]:
            labels.setdefault(r["label"], []).append(r)
    if not labels:
        return None, []
    asked = set(words(question))
    best = max(labels, key=lambda l: (len(asked & set(words(l))), len(labels[l])))
    return best, labels[best]


def series_of(rows):
    """One value per date (the latest row wins for a duplicated date), sorted by date."""
    by_date = {}
    for r in rows:
        by_date[r["date"]] = r
    keys = sorted(by_date)
    return keys, [by_date[k]["amount"] for k in keys], [by_date[k] for k in keys]


def spacing_days(dates):
    if len(dates) < 2:
        return None
    d = [date.fromisoformat(b).toordinal() - date.fromisoformat(a).toordinal() for a, b in zip(dates, dates[1:])]
    return st.median(d)


def max_drawdown(dates, values):
    peak_i, worst = 0, (0.0, 0, 0)
    for i, v in enumerate(values):
        if v > values[peak_i]:
            peak_i = i
        if values[peak_i] > 0:
            dd = (v - values[peak_i]) / values[peak_i]
            if dd < worst[0]:
                worst = (dd, peak_i, i)
    dd, p, t = worst
    if dd == 0:
        return None
    recovered = next((dates[j] for j in range(t, len(values)) if values[j] >= values[p]), None)
    return {"pct": dd * 100, "peak": dates[p], "trough": dates[t], "recovered": recovered}


def linear_fit(values):
    n = len(values)
    xs = range(n)
    mx, my = (n - 1) / 2, sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / sxx
    ss_tot = sum((y - my) ** 2 for y in values) or 1.0
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, values))
    return slope, max(0.0, 1 - ss_res / ss_tot)


def moving_average(values, k):
    return sum(values[-k:]) / k if len(values) >= k else None


def analyse(rows, question, fmt):
    """(text, table, chart) for the question's measure, or None if there is no dated series of 3+ points.
    fmt(value) formats a number in the measure's unit (currency or plain)."""
    label, picked = pick_measure(rows, question)
    if not picked:
        return None
    dates, values, recs = series_of(picked)
    if len(values) < 3:
        return None
    n = len(values)
    gap = spacing_days(dates) or 1
    unit = "day" if gap <= 3 else "week" if gap <= 10 else "month" if gap <= 45 else "quarter" if gap <= 120 else "year"
    rets = [(b - a) / a for a, b in zip(values, values[1:]) if a]
    stats = [("Points", f"{n} ({dates[0]} to {dates[-1]}, about one per {unit})")]
    change = (values[-1] - values[0]) / values[0] * 100 if values[0] else None
    stats += [("First", f"{fmt(values[0])} on {dates[0]}"), ("Last", f"{fmt(values[-1])} on {dates[-1]}")]
    if change is not None:
        stats.append(("Change", f"{change:+.1f}%"))
    hi, lo = max(range(n), key=values.__getitem__), min(range(n), key=values.__getitem__)
    stats += [("Mean", fmt(st.mean(values))), ("Median", fmt(st.median(values))),
              ("Highest", f"{fmt(values[hi])} on {dates[hi]}"), ("Lowest", f"{fmt(values[lo])} on {dates[lo]}"),
              ("Standard deviation", fmt(st.pstdev(values)))]
    if len(rets) >= 2:
        vol = st.pstdev(rets) * 100
        per_year = {"day": 252, "week": 52, "month": 12, "quarter": 4, "year": 1}[unit]
        stats.append(("Volatility", f"{vol:.2f}% per {unit}" + (f" ({vol * math.sqrt(per_year):.1f}% a year)"
                                                                 if per_year > 1 else "")))
        bi, wi = max(range(len(rets)), key=rets.__getitem__), min(range(len(rets)), key=rets.__getitem__)
        stats += [(f"Best {unit}", f"{rets[bi] * 100:+.1f}% to {dates[bi + 1]}"),
                  (f"Worst {unit}", f"{rets[wi] * 100:+.1f}% to {dates[wi + 1]}")]
    dd = max_drawdown(dates, values)
    if dd:
        stats.append(("Largest fall from a peak", f"{dd['pct']:.1f}% ({dd['peak']} to {dd['trough']}, "
                      + (f"recovered {dd['recovered']})" if dd["recovered"] else "not yet recovered)")))
    slope, r2 = linear_fit(values)
    strength = "strong" if r2 >= 0.7 else "moderate" if r2 >= 0.4 else "weak"
    direction = "upward" if slope > 0 else "downward"
    stats.append(("Trend", f"{direction}, {strength} (straight-line fit R² = {r2:.2f}, "
                           f"{fmt(abs(slope))} per {unit})"))
    for k in (20, 50):
        ma = moving_average(values, k)
        if ma is not None:
            stats.append((f"{k}-{unit} average", f"{fmt(ma)} (latest is {'above' if values[-1] >= ma else 'below'} it)"))
    months = {}
    for d, v in zip(dates, values):
        months.setdefault(int(d[5:7]), []).append(v)
    if len({d[:7] for d in dates}) >= 12 and len(months) == 12:
        avg = {m: st.mean(v) for m, v in months.items()}
        top, low = max(avg, key=avg.get), min(avg, key=avg.get)
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        stats.append(("Seasonality", f"highest on average in {names[top - 1]} ({fmt(avg[top])}), "
                                     f"lowest in {names[low - 1]} ({fmt(avg[low])})"))
    text = (f"{label}: {direction} trend ({strength}), "
            + (f"{change:+.1f}% " if change is not None else "")
            + f"from {dates[0]} to {dates[-1]} over {n} points"
            + (f"; largest fall {dd['pct']:.1f}%" if dd else "") + ".")
    table = {"columns": [{"name": "Measure", "type": "text"}, {"name": "Value", "type": "text"}],
             "rows": [list(x) for x in stats], "text": [list(x) for x in stats],
             "sources": [[{"path": recs[0]["doc"], "quote": recs[0]["text"]}] for _ in stats]}
    step = max(1, math.ceil(n / MAX_POINTS))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    chart = {"type": "line", "y": {"name": label, "unit": recs[0].get("currency") or ""},
             "points": [{"x": dates[i], "value": round(values[i], 4), "rows": []} for i in idx]}
    return text, table, chart
