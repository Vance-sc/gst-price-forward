#!/usr/bin/env python3
"""Regression tests for generate.py (v2 engine). Standard library only.
Fixture rows use the exact schema observed live on the LMR DataMart,
including the ".00" no-trade rows that broke production v0."""

import math
import datetime as dt
import generate as g

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- parsing
def row(sec, date, item, wavg, lbs, trades):
    return {"_section": sec, "report_date": date, "item_description": item,
            "weighted_average": wavg, "total_pounds": lbs,
            "number_trades": trades, "report_title": "LM_XB403"}


FIX = [
    row("Choice Cuts", "07/01/2026", "Chuck, roll, lxl, neck/off (116A  3)", "480.41", "347,229", "59"),
    row("Choice Cuts", "07/01/2026", "Chuck, roll, retail ready (916A  3)", ".00", "", ""),        # no-trade
    row("Select Cuts", "07/01/2026", "Chuck, roll, lxl, neck/off (116A  3)", "470.36", "13,112", "9"),  # wrong grade
    row("Choice Cuts", "07/01/2026", "Round, bone-in (160  1)", None, None, None),                 # null
    row("Choice Cuts", "07/01/2026", "Chuck, flap (116G  4)", "1,005.65", "23,850", "20"),         # wrong cut
    row("Choice Cuts", "07/01/2026", "Chuck, clod tender (114F  5)", "807.01", "18,834", "23"),    # wrong cut
    row("Choice Cuts", "07/01/2026", "Loin, bottom sirloin, flap (185A  4)", "946.89", "33,134", "18"),
    row("Choice Cuts", "07/01/2026", "Chuck, shoulder clod, trmd (114A  3)", "438.84", "91,896", "28"),
    row("Choice Cuts", "07/01/2026", "Short Plate, short rib (123A  3)", "674.03", "28,170", "21"),
    row("Choice Cuts", "07/01/2026", "Round, knuckle, peeled (167A  4)", "464.40", "179,941", "32"),
    row("Choice Cuts", "06/30/2026", "Round, knuckle, peeled (167A  4)", "400.00", "100,000", "10"),
    row("Choice Cuts", "06/30/2026", "Round, knuckle, peeled (167A  4)", "500.00", "300,000", "30"),
    {"_section": "Current Cutout Values", "report_date": "07/01/2026",
     "choice_600_900_current": "391.26", "select_600_900_current": "369.69"},
    {"_section": "Current Cutout Values", "report_date": "06/30/2026",
     "choice_600_900_current": "390.03", "select_600_900_current": ".00"},
]

series, cutout = g.parse_rows(FIX)
d = dt.date(2026, 7, 1)


def one(key, want_price, want_lbs):
    pts = series[key]
    return (len(pts) == 1 and pts[0][0] == d
            and abs(pts[0][1] - want_price) < 1e-6
            and abs(pts[0][2] - want_lbs) < 1e-6)


check("chuck_roll = Choice 116A only, lbs kept",
      one("chuck_roll", 480.41, 347229), str(series["chuck_roll"]))
check("regression: NOT the zero-polluted 237.6925",
      abs(series["chuck_roll"][0][1] - 237.6925) > 1)
check("flap ignores 'Chuck, flap'", one("flap", 946.89, 33134))
check("clod ignores clod tender", one("shoulder_clod", 438.84, 91896))
check("costilla = plate 123A", one("short_rib", 674.03, 28170))
check("knuckle latest = 464.40",
      series["round"][-1][0] == d and abs(series["round"][-1][1] - 464.40) < 1e-6)
vw = series["round"][0]
check("duplicates volume-weighted (475) and lbs summed (400k)",
      abs(vw[1] - 475.0) < 1e-9 and vw[2] == 400000, str(vw))
check("cutout parsed, select .00 -> None",
      cutout == [(dt.date(2026, 6, 30), 390.03, None), (d, 391.26, 369.69)])

# ---------------------------------------------------------------- v2 signals
# synthetic 600-day series against a flat cutout: prices flat at 400,
# then a 10% spike at the end -> rich vs cutout + strong up-momentum
# -> BOTH rel-value and contrarian momentum components must go LOW.
today = dt.date.today()
days = [today - dt.timedelta(days=i) for i in range(900, 0, -1)]
days = [x for x in days if x.weekday() < 5]
cut_flat = [(x, 400.0, 385.0) for x in days]

flat_spike = [(x, 400.0 + (40.0 if i >= len(days) - 5 else 0.0), 50000)
              for i, x in enumerate(days)]
F = g.build_features(flat_spike, cut_flat)
i_last = len(flat_spike) - 1
sc, det = g.score_at(F, i_last, 30)
check("v2: rich+rallying -> rel_value component low",
      det["rel_value"] < 25, str(det["rel_value"]))
check("v2: rich+rallying -> momentum component low (contrarian)",
      det["momentum"] < 25, str(det["momentum"]))
check("v2: rich+rallying -> composite below HOLD threshold",
      sc < g.THRESHOLDS[30]["hold"], f"{sc:.1f}")

# mirror: a 10% dip -> cheap vs cutout + down-momentum -> high score
flat_dip = [(x, 400.0 - (40.0 if i >= len(days) - 5 else 0.0), 50000)
            for i, x in enumerate(days)]
F2 = g.build_features(flat_dip, cut_flat)
sc2, det2 = g.score_at(F2, i_last, 30)
check("v2: cheap+dipping -> composite above LOCK threshold",
      sc2 >= g.THRESHOLDS[30]["lock"], f"{sc2:.1f}")

# volume: surge in recent lbs with flat price -> volume component high
vol_surge = [(x, 400.0, 50000 if i < len(days) - 15 else 250000)
             for i, x in enumerate(days)]
F3 = g.build_features(vol_surge, cut_flat)
_sc3, det3 = g.score_at(F3, i_last, 30)
check("v2: volume surge -> volume component high",
      det3["volume"] > 75, str(det3["volume"]))

# insufficient history -> None
F4 = g.build_features(flat_spike[:120], cut_flat)
sc4, _ = g.score_at(F4, 119, 30)
check("v2: thin history -> no score", sc4 is None)

# horizons use different momentum windows: ramp over the last 35 obs makes
# the 21-obs and 42-obs rates of change diverge
ramp = [(x, 400.0 + max(0, i - (len(days) - 35)) * 1.5, 50000)
        for i, x in enumerate(days)]
F5 = g.build_features(ramp, cut_flat)
check("v2: 30d and 60d momentum use different windows",
      abs(F5["roc30"][i_last] - F5["roc60"][i_last]) > 1.0,
      f"{F5['roc30'][i_last]:.1f} vs {F5['roc60'][i_last]:.1f}")

# ---------------------------------------------------------------- labels/validation
check("label LOCK at 30d threshold",
      g.label_for(g.THRESHOLDS[30]["lock"], 30)[0] == "LOCK")
check("label HOLD below 30d hold threshold",
      g.label_for(g.THRESHOLDS[30]["hold"] - 0.1, 30)[0] == "HOLD")
v, conf = g.validation_for("chuck_roll", 60, "LOCK")
check("validation lookup: chuck_roll 60d LOCK hit .70 -> High",
      v["hit"] == 0.70 and conf == "High", f"{v} {conf}")
v2_, conf2 = g.validation_for("chuck_roll", 60, "HOLD")
check("validation: HOLD low hit rate = High confidence (inverse)",
      conf2 == "High", f"{v2_} {conf2}")
v3_, conf3 = g.validation_for("shoulder_clod", 30, "LOCK")
check("validation: clod 30d weak edge -> Low", conf3 == "Low", str(v3_))
vp, _ = g.validation_for("nonexistent", 30, "SPLIT")
check("validation: pooled fallback works", vp["n"] == 235, str(vp))

check("weights sum to 1.0", abs(sum(g.WEIGHTS.values()) - 1.0) < 1e-9)

# ---------------------------------------------------------------- render
html = g.render_html({"meta": {"x": "</script><script>alert(1)</script>"},
                      "products": {}})
check("render escapes </script>", "</script><script>alert(1)" not in html)
check("render injects APP_DATA", "window.APP_DATA" in html)

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES: {FAILS}")
    raise SystemExit(1)
print("ALL TESTS PASSED")
