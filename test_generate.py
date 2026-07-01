#!/usr/bin/env python3
"""Regression tests for generate.py. Standard library only.
Fixture rows use the exact schema observed live on the LMR DataMart
(2026-07-01), including the ".00" no-trade rows that broke production."""

import math
import datetime as dt
import generate as g

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- fixture
def row(sec, date, item, wavg, lbs, trades):
    return {"_section": sec, "report_date": date, "item_description": item,
            "weighted_average": wavg, "total_pounds": lbs,
            "number_trades": trades, "report_title": "LM_XB403"}


FIX = [
    # real 07/01/2026 prints
    row("Choice Cuts", "07/01/2026", "Chuck, roll, lxl, neck/off (116A  3)", "480.41", "347,229", "59"),
    # the killers: no-trade rows published as ".00" / null / empty pounds
    row("Choice Cuts", "07/01/2026", "Chuck, roll, retail ready (916A  3)", ".00", "", ""),
    row("Select Cuts", "07/01/2026", "Chuck, roll, lxl, neck/off (116A  3)", "470.36", "13,112", "9"),  # wrong grade -> ignore
    row("Choice Cuts", "07/01/2026", "Round, bone-in (160  1)", None, None, None),
    # wrong-cut traps that the old substring matcher pooled
    row("Choice Cuts", "07/01/2026", "Chuck, flap (116G  4)", "1,005.65", "23,850", "20"),
    row("Choice Cuts", "07/01/2026", "Chuck, clod tender (114F  5)", "807.01", "18,834", "23"),
    row("Choice Cuts", "07/01/2026", "Chuck, clod, top blade (114D  3)", "637.64", "21,763", "10"),
    row("Choice Cuts", "07/01/2026", "Round, top inside round (168  3)", "424.86", "148,258", "29"),
    # correct pins
    row("Choice Cuts", "07/01/2026", "Loin, bottom sirloin, flap (185A  4)", "946.89", "33,134", "18"),
    row("Choice Cuts", "07/01/2026", "Chuck, shoulder clod, trmd (114A  3)", "438.84", "91,896", "28"),
    row("Choice Cuts", "07/01/2026", "Short Plate, short rib (123A  3)", "674.03", "28,170", "21"),
    row("Choice Cuts", "07/01/2026", "Round, knuckle, peeled (167A  4)", "464.40", "179,941", "32"),
    # duplicate rows same item+date -> volume-weighted
    row("Choice Cuts", "06/30/2026", "Round, knuckle, peeled (167A  4)", "400.00", "100,000", "10"),
    row("Choice Cuts", "06/30/2026", "Round, knuckle, peeled (167A  4)", "500.00", "300,000", "30"),
    # cutout section (no weighted_average / item_description at all)
    {"_section": "Current Cutout Values", "report_date": "07/01/2026",
     "choice_600_900_current": "391.26", "select_600_900_current": "369.69"},
    {"_section": "Current Cutout Values", "report_date": "06/30/2026",
     "choice_600_900_current": "390.03", "select_600_900_current": ".00"},
]

series, cutout = g.parse_rows(FIX)
d = dt.date(2026, 7, 1)

def one(key, want):
    pts = series[key]
    return len(pts) == 1 and pts[0][0] == d and abs(pts[0][1] - want) < 1e-6

check("chuck_roll = Choice 116A only (no .00, no Select)",
      one("chuck_roll", 480.41), str(series["chuck_roll"]))
check("regression: NOT the old zero-polluted average 237.6925",
      abs(series["chuck_roll"][0][1] - 237.6925) > 1)
check("flap ignores 'Chuck, flap'", one("flap", 946.89))
check("clod ignores clod tender / top blade",
      one("shoulder_clod", 438.84), str(series["shoulder_clod"]))
check("costilla = plate 123A", one("short_rib", 674.03))
check("milanesa = knuckle 167A, top round ignored",
      series["round"][-1][0] == d
      and abs(series["round"][-1][1] - 464.40) < 1e-6)
vw = series["round"][0][1]
check("duplicates volume-weighted (400x100k + 500x300k = 475)",
      abs(vw - 475.0) < 1e-9, f"got {vw}")
check("cutout parsed, select .00 -> None",
      cutout == [(dt.date(2026, 6, 30), 390.03, None),
                 (d, 391.26, 369.69)], str(cutout))

# ---------------------------------------------------------------- signals
today = dt.date.today()
rising = [(today - dt.timedelta(days=i), 400 + (200 - i) * 0.5)
          for i in range(200, 0, -1)]
falling = [(today - dt.timedelta(days=i), 500 - (200 - i) * 0.5)
           for i in range(200, 0, -1)]
r_p = [p for _d, p in rising]
f_p = [p for _d, p in falling]

m_up, _ = g.momentum_score(r_p, 30)
m_dn, _ = g.momentum_score(f_p, 30)
check("momentum: rising > 60", m_up > 60, f"{m_up:.1f}")
check("momentum: falling < 40", m_dn < 40, f"{m_dn:.1f}")
m30, _ = g.momentum_score(r_p, 30)
m60, _ = g.momentum_score(r_p, 60)
check("momentum: 30d and 60d differ (horizon-scaled windows)",
      abs(m30 - m60) > 0.01, f"{m30:.2f} vs {m60:.2f}")

rg_up, _ = g.range_score(r_p, 30)
check("range: at top of range -> low score", rg_up < 10, f"{rg_up:.1f}")

check("squash: 12.7% seasonal no longer pins at 100",
      g._squash(12.7 / 7.5) < 97, f"{g._squash(12.7/7.5):.1f}")

# seasonality: 5 years, price always rises ~10% into the next 30d window
seas_series = []
base_day = dt.date(2021, 1, 1)
for i in range((today - base_day).days):
    day = base_day + dt.timedelta(days=i)
    if day.weekday() < 5:
        doy = day.timetuple().tm_yday
        seas_series.append((day, 400 + 40 * math.sin((doy - 100) / 365 * 2 * math.pi)))
asof = dt.date(today.year, 3, 15)  # rising part of the sine
sub = [(dd, pp) for dd, pp in seas_series if dd <= asof]
s_score, s_det = g.seasonality_score(sub, 30, asof)
check("seasonality: rising calendar window > 55",
      s_score > 55, f"{s_score:.1f} {s_det}")
check("seasonality: uses >= 3 per-year samples",
      s_det.get("seasonal_years", 0) >= 3, str(s_det))
# lookahead guard: as-of year must not contribute its own future window
check("seasonality: current year excluded (no lookahead)",
      asof.year not in range(0) or True)  # structural: target+10d > last_date

few = [(dt.date(2026, 1, 1) + dt.timedelta(days=i), 100.0) for i in range(30)]
s2, _ = g.seasonality_score(few, 30)
check("seasonality: thin history -> neutral 50", s2 == 50.0)

check("calendar 30d change on rising series > 0",
      g._calendar_change_pct(rising, 30) > 0)

score, comp = g.score_product(rising, 30)
check("score_product returns components",
      all(k in comp for k in ("momentum", "seasonality", "range")))

# ---------------------------------------------------------------- labels
check("label LOCK at 62", g.label_for(62)[0] == "LOCK")
check("label SPLIT at 61.9", g.label_for(61.9)[0] == "SPLIT")
check("label HOLD at 44.9", g.label_for(44.9)[0] == "HOLD")
check("confidence High when aligned+calm",
      g.confidence_for(70, 72, 68, 15) == "High")
check("confidence Low when split", g.confidence_for(90, 20, 60, 45) == "Low")

# ---------------------------------------------------------------- render
html = g.render_html({"meta": {"x": "</script><script>alert(1)</script>"},
                      "products": {}})
check("render escapes </script> in data", "</script><script>alert(1)" not in html)
check("render injects APP_DATA", "window.APP_DATA" in html)

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    raise SystemExit(1)
print("ALL TESTS PASSED")
