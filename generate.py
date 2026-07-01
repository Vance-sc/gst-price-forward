#!/usr/bin/env python3
"""
GST Meat Co. — Forward-Buy Signal Tool
=======================================
Pulls USDA boxed-beef cut prices from the LMR DataMart (report LM_XB403,
slug 2453), computes trend / seasonality / range signals, and produces a
"Lock Score" for 30- and 60-day forward-buy decisions.

Output: data.json (computed series + signals) and index.html (dashboard).

Data source
-----------
LMR DataMart (no API key required):
  https://mpr.datamart.ams.usda.gov/services/v1.1/reports/2453/<Section>
Sections are path segments ("Choice Cuts", "Select Cuts", "Current Cutout
Values"). Date filtering uses  ?q=report_date=MM/DD/YYYY:MM/DD/YYYY
(the report's own date column is report_date; report_begin_date is rejected).

IMPORTANT — always use the date-range filter. An unfiltered section pull is
capped at 100,000 rows (newest-first), which silently slides the start of
history forward every day and downloads ~90 MB per section.

Data hygiene rules (learned the hard way):
  * No-trade rows are published with weighted_average ".00" — they are NOT
    zero prices and must be discarded.
  * Each product pins to ONE exact item_description + ONE grade section.
    Substring matching pooled wrong cuts and mixed Choice with Select,
    which produced garbage averages.

Modes
-----
LIVE (default) : fetches the real DataMart data. No key needed.
DEMO           : set FORCE_DEMO=1, or automatic fallback if the live fetch
                 fails. Synthetic data, clearly flagged on every screen.

Honesty note
------------
This tool does NOT predict prices. It measures whether the market is under
upward or downward pressure and how the calendar has historically behaved,
then translates that into a lean. Treat it as decision support for a
Cargill / Zant lock conversation, not a forecast. Signal weights and
thresholds are heuristics pending calibration — run backtest.py.
"""

import os
import json
import math
import datetime as dt
from urllib import request, parse, error

# ---------------------------------------------------------------------------
# CONFIG — what we track
# ---------------------------------------------------------------------------
REPORT_ID = "2453"   # LMR DataMart slug for LM_XB403 (boxed beef PM)
API_BASE = "https://mpr.datamart.ams.usda.gov/services/v1.1/reports/"

# GST's top 5 beef products, each pinned to ONE exact USDA item + grade.
# item strings are verbatim from the API — note the double spaces before
# the trailing spec number. To remap a product, replace the whole item
# string with another verbatim item_description from the section.
PRODUCTS = [
    {"key": "chuck_roll",
     "name": "Diesmillo — Chuck Roll",
     "unit": "$/cwt",
     "item": "Chuck, roll, lxl, neck/off (116A  3)",
     "section": "Choice Cuts",
     "spec": "USDA 116A · Choice"},
    {"key": "flap",
     "name": "Fajita de Res — Sirloin Flap (Bavette)",
     "unit": "$/cwt",
     "item": "Loin, bottom sirloin, flap (185A  4)",
     "section": "Choice Cuts",
     "spec": "USDA 185A · Choice"},
    {"key": "shoulder_clod",
     "name": "Espaldia — Shoulder Clod (Trimmed)",
     "unit": "$/cwt",
     "item": "Chuck, shoulder clod, trmd (114A  3)",
     "section": "Choice Cuts",
     "spec": "USDA 114A · Choice"},
    # Costilla pins to PLATE short rib (123A, ribs 6-8, under the ribeye).
    # USDA quotes only two short-rib items; the other is the chuck-end cut:
    #   "Chuck, short rib (130  4)"  spec "USDA 130 · Choice"
    # Swap the strings if GST's invoices turn out to show 130.
    {"key": "short_rib",
     "name": "Costilla — Plate Short Rib",
     "unit": "$/cwt",
     "item": "Short Plate, short rib (123A  3)",
     "section": "Choice Cuts",
     "spec": "USDA 123A · Choice"},
    {"key": "round",
     "name": "Milanesa — Knuckle (Peeled)",
     "unit": "$/cwt",
     "item": "Round, knuckle, peeled (167A  4)",
     "section": "Choice Cuts",
     "spec": "USDA 167A · Choice"},
]

CUTOUT_SECTION = "Current Cutout Values"

# GST annual POUNDS per product. Pounds only here so this file is safe in a
# public repo. Confidential sales/margin/cost figures live in gst_private.py
# (gitignored, never uploaded); when present locally it fills in the dollar
# detail for the full local dashboard.
GST_CONTEXT = {
    "chuck_roll":    {"lbs": 511841},
    "flap":          {"lbs": 200510},
    "shoulder_clod": {"lbs": 74061},
    "short_rib":     {"lbs": 56158},
    "round":         {"lbs": 35926},
}
try:  # local-only overlay with sales/margin/cost — not committed
    from gst_private import GST_CONTEXT_FULL
    for _k, _v in GST_CONTEXT_FULL.items():
        GST_CONTEXT.setdefault(_k, {}).update(_v)
except ImportError:
    pass

# Years of history to fetch. Seasonality needs several full years; 5 keeps
# the pull small (~3-4 MB/section) and comfortably under the 100k-row cap.
FETCH_YEARS = 5

# Signal component weights (must sum to 1.0). Range position is deliberately
# the lightest: it is a mean-reversion bet and fights momentum in trends.
# These are heuristics — calibrate with backtest.py before leaning on them.
WEIGHTS = {"momentum": 0.45, "seasonality": 0.35, "range": 0.20}

LOCK_THRESHOLD, HOLD_THRESHOLD = 62, 45   # pending backtest calibration

# Trim the published per-product series to this many observations (the chart
# shows ~180). Full history stays in memory for the seasonality math only.
PUBLISH_POINTS = 270

# Warn on the dashboard if the newest market date is older than this many
# business days (holidays produce 1-2 day gaps; more means something broke).
STALE_BUSINESS_DAYS = 4

# When set (e.g. in GitHub Actions), the published build omits GST's
# confidential dollar figures/margins — pounds only. Local runs show detail.
PUBLIC_BUILD = bool(os.environ.get("PUBLIC_BUILD"))
FORCE_DEMO = bool(os.environ.get("FORCE_DEMO"))


# ---------------------------------------------------------------------------
# DATA FETCH — live
# ---------------------------------------------------------------------------
def _extract_rows(payload):
    """Pull the data-row list out of a DataMart section payload."""
    if isinstance(payload, dict):
        v = payload.get("results")
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        rows = []
        for v in payload.values():
            if isinstance(v, list):
                rows.extend([x for x in v if isinstance(x, dict)])
        return rows
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _fetch_section(section, begin, end, tries=3, timeout=90):
    """Fetch one DataMart section with a report_date range filter.
    Retries with backoff. Returns (rows, error_message_or_None)."""
    dr = f"{begin:%m/%d/%Y}:{end:%m/%d/%Y}"
    url = (API_BASE + REPORT_ID + "/" + parse.quote(section)
           + "?q=report_date=" + parse.quote(dr, safe="/:"))
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            with request.urlopen(request.Request(url), timeout=timeout) as r:
                payload = json.loads(r.read().decode())
            rows = _extract_rows(payload)
            if rows:
                print(f"SECTION '{section}': {len(rows)} rows "
                      f"(attempt {attempt})")
                return rows, None
            last_err = "0 rows returned"
        except error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as ex:                     # noqa: BLE001
            last_err = str(ex)
        print(f"SECTION '{section}' attempt {attempt}: {last_err}")
        if attempt < tries:
            import time
            time.sleep(5 * attempt)
    return [], f"section '{section}' failed after {tries} tries: {last_err}"


def fetch_live():
    """Fetch every section any PRODUCT needs, plus the cutout section.
    Returns (rows, warnings). rows carry a '_section' tag."""
    end = dt.date.today()
    begin = end - dt.timedelta(days=int(FETCH_YEARS * 365.25) + 40)
    sections = sorted({p["section"] for p in PRODUCTS}) + [CUTOUT_SECTION]
    all_rows, warnings = [], []
    for sec in sections:
        rows, err = _fetch_section(sec, begin, end)
        if err:
            warnings.append(err)
        for row in rows:
            row["_section"] = sec
        all_rows.extend(rows)
    print(f"TOTAL rows: {len(all_rows)}")
    return all_rows, warnings


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_date(v):
    s = str(v)[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_rows(rows):
    """Turn raw DataMart rows into per-product series and a cutout series.

    Returns (series, cutout) where
      series = {product_key: [(date, price), ...] sorted}
      cutout = [(date, choice_cutout, select_cutout), ...] sorted

    Hygiene enforced here:
      * exact match on item_description AND section — no substring pooling
      * rows with missing/zero weighted_average are no-trade rows: DISCARDED
      * rows with no positive total_pounds: DISCARDED
      * duplicate rows for the same product+date are volume-weighted
    """
    want = {(p["item"], p["section"]): p["key"] for p in PRODUCTS}
    acc = {p["key"]: {} for p in PRODUCTS}    # key -> date -> [(price, lbs)]
    cut = {}                                   # date -> (choice, select)
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = _parse_date(row.get("report_date"))
        if not date:
            continue
        if row.get("_section") == CUTOUT_SECTION:
            ch = _num(row.get("choice_600_900_current"))
            se = _num(row.get("select_600_900_current"))
            if ch and ch > 0:
                cut[date] = (ch, se if (se and se > 0) else None)
            continue
        key = want.get((row.get("item_description"), row.get("_section")))
        if not key:
            continue
        price = _num(row.get("weighted_average"))
        lbs = _num(row.get("total_pounds"))
        if price is None or price <= 0:      # ".00" / null = no trades
            continue
        if lbs is None or lbs <= 0:
            continue
        acc[key].setdefault(date, []).append((price, lbs))
    series = {}
    for key, dmap in acc.items():
        pts = []
        for d, entries in dmap.items():
            wsum = sum(l for _p, l in entries)
            pts.append((d, sum(p * l for p, l in entries) / wsum))
        series[key] = sorted(pts)
    cutout = sorted((d, c, s) for d, (c, s) in cut.items())
    print("mapped points:", {k: len(v) for k, v in series.items()})
    return series, cutout


# ---------------------------------------------------------------------------
# DATA FETCH — demo (synthetic but realistic)
# ---------------------------------------------------------------------------
def generate_demo():
    """Synthetic daily series so the dashboard is viewable offline.

    Base levels are generic ballpark figures from public USDA prints —
    NOT GST figures. Seasonal peak late May/June, winter trough, slow
    uptrend, daily noise. Clearly flagged as SAMPLE DATA in the UI.
    """
    import random
    random.seed(42)
    end = dt.date.today()
    days = int(FETCH_YEARS * 365.25)
    dates = [end - dt.timedelta(days=i) for i in range(days)][::-1]
    dates = [d for d in dates if d.weekday() < 5]

    def seasonal(d):
        doy = d.timetuple().tm_yday
        return math.sin((doy - 55) / 365 * 2 * math.pi)

    base_levels = {   # generic public USDA ballparks, $/cwt
        "chuck_roll": 480, "flap": 950, "shoulder_clod": 440,
        "short_rib": 675, "round": 465,
    }
    amp = {
        "chuck_roll": 35, "flap": 85, "shoulder_clod": 22,
        "short_rib": 55, "round": 28,
    }
    out = {}
    for key, base in base_levels.items():
        level = base * 0.7    # start lower; multi-year uptrend brings it up
        vals = []
        for i, d in enumerate(dates):
            trend = i * (base * 0.3 / len(dates))
            seas = amp[key] * seasonal(d)
            level += random.gauss(0, 1.4)
            level = 0.995 * level + 0.005 * base * 0.7
            vals.append((d, round(level + trend + seas
                                  + random.gauss(0, 2.0), 2)))
        out[key] = vals
    cutout = [(d, round(300 + i * 0.06 + 25 * seasonal(d)
                        + random.gauss(0, 2), 2), None)
              for i, d in enumerate(dates)]
    return out, cutout


# ---------------------------------------------------------------------------
# SIGNAL MATH
# ---------------------------------------------------------------------------
def _sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _squash(x):
    """Map an unbounded signal to 0-100 without hard clamping.
    tanh keeps extreme readings distinguishable instead of pinning at 100."""
    return 50 + 50 * math.tanh(x)


def momentum_score(prices, horizon_days):
    """0-100. >50 => upward pressure (favors locking).
    Windows scale with the horizon so 30d and 60d are real, distinct reads."""
    if horizon_days <= 30:
        short_n, long_n, roc_n = 10, 40, 21
    else:
        short_n, long_n, roc_n = 20, 80, 42
    need = max(long_n, roc_n + 1)
    if len(prices) < need:
        return 50.0, {}
    short = _sma(prices, short_n)
    long_ = _sma(prices, long_n)
    spread = (short - long_) / long_ * 100 if long_ else 0.0
    base = prices[-roc_n - 1]
    roc = (prices[-1] - base) / base * 100 if base else 0.0
    raw = _squash(spread / 2.5 + roc / 10.0)
    return _clamp(raw), {"ma_spread_pct": round(spread, 2),
                         "roc_pct": round(roc, 2), "roc_days": roc_n,
                         "sma_short": round(short, 2),
                         "sma_long": round(long_, 2)}


def _window_mean(price_by_date, center, half_width=10):
    acc = []
    for off in range(-half_width, half_width + 1):
        p = price_by_date.get(center + dt.timedelta(days=off))
        if p is not None:
            acc.append(p)
    return (sum(acc) / len(acc), len(acc)) if acc else (None, 0)


def seasonality_score(series, horizon_days, asof=None):
    """0-100 from PER-YEAR calendar moves, not pooled price levels.

    For each prior year: % change from the ±10-day window around this
    calendar date to the window `horizon_days` later, computed within that
    year. Per-year percent changes are then averaged, so a $240 year and a
    $480 year contribute equally. Needs >= 3 usable years, else neutral 50.
    Passing `asof` (and a truncated series) keeps backtests lookahead-free.
    """
    if not series:
        return 50.0, {}
    asof = asof or series[-1][0]
    last_date = series[-1][0]
    price_by_date = dict(series)
    first_year = series[0][0].year
    pcts, years_used = [], []
    for y in range(first_year, asof.year + 1):
        try:
            anchor = asof.replace(year=y)
        except ValueError:                     # Feb 29 in a non-leap year
            anchor = asof.replace(year=y, day=28)
        target = anchor + dt.timedelta(days=horizon_days)
        if target + dt.timedelta(days=10) > last_date:
            continue                           # ahead window incomplete
        now_avg, n1 = _window_mean(price_by_date, anchor)
        ahead_avg, n2 = _window_mean(price_by_date, target)
        if now_avg and ahead_avg and n1 >= 5 and n2 >= 5:
            pcts.append((ahead_avg - now_avg) / now_avg * 100)
            years_used.append(y)
    if len(pcts) < 3:
        return 50.0, {"seasonal_years": len(pcts)}
    mean_pct = sum(pcts) / len(pcts)
    raw = _squash(mean_pct / 7.5)
    return _clamp(raw), {"seasonal_chg_pct": round(mean_pct, 2),
                         "seasonal_years": len(pcts),
                         "per_year_pct": [round(p, 1) for p in pcts]}


def range_score(prices, horizon_days):
    """0-100. Where does today sit in its recent range?
    Low in range = higher score. NOTE: this is a mean-reversion bet — it
    fights momentum in sustained trends, which is why its weight is low."""
    lookback = 120 if horizon_days <= 30 else 240
    window = prices[-lookback:] if len(prices) >= lookback else prices
    lo, hi = min(window), max(window)
    if hi == lo:
        return 50.0, {}
    pct = (prices[-1] - lo) / (hi - lo)
    return _clamp((1 - pct) * 100), {"range_pctile": round(pct * 100, 1),
                                     "low": round(lo, 2),
                                     "high": round(hi, 2),
                                     "lookback_obs": len(window)}


def volatility(prices, n=30):
    if len(prices) < n + 1:
        return 0.0
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(len(prices) - n, len(prices)) if prices[i - 1]]
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(252) * 100    # annualized %, rough


def label_for(score):
    if score >= LOCK_THRESHOLD:
        return ("LOCK", "Upward pressure — locking looks favorable")
    if score >= HOLD_THRESHOLD:
        return ("SPLIT", "Mixed signals — consider locking part of the volume")
    return ("HOLD", "Soft / downward — little urgency to lock now")


def confidence_for(mom, seas, rng, vol):
    """Low/Medium/High from component agreement + volatility.
    Vol bands sized for clean single-cut boxed-beef series (typically
    ~15-40% annualized)."""
    scores = [mom, seas, rng]
    spread = max(scores) - min(scores)
    same_side = all(s >= 50 for s in scores) or all(s < 50 for s in scores)
    if same_side and spread < 25 and vol < 30:
        return "High"
    if spread < 45 and vol < 50:
        return "Medium"
    return "Low"


def score_product(series, horizon_days, asof=None):
    """Composite lock score for one product/horizon. Used by generate and
    backtest.py (pass truncated series + asof for walk-forward)."""
    prices = [p for _d, p in series]
    mom, mom_d = momentum_score(prices, horizon_days)
    rng, rng_d = range_score(prices, horizon_days)
    seas, seas_d = seasonality_score(series, horizon_days, asof)
    score = (WEIGHTS["momentum"] * mom + WEIGHTS["seasonality"] * seas
             + WEIGHTS["range"] * rng)
    return score, {"momentum": mom, "seasonality": seas, "range": rng,
                   "momentum_detail": mom_d, "range_detail": rng_d,
                   "seasonality_detail": seas_d}


# ---------------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------------
def _calendar_change_pct(series, days):
    """% change vs the last price on/before `days` calendar days ago."""
    if len(series) < 2:
        return 0.0
    target = series[-1][0] - dt.timedelta(days=days)
    past = None
    for d, p in reversed(series):
        if d <= target:
            past = p
            break
    if not past:
        return 0.0
    return round((series[-1][1] - past) / past * 100, 1)


def analyze(series):
    results = {}
    for prod in PRODUCTS:
        key = prod["key"]
        pts = series.get(key, [])
        if len(pts) < 90:
            continue
        prices = [p for _d, p in pts]
        vol = volatility(prices)
        horizons = {}
        for h in (30, 60):
            score, comp = score_product(pts, h)
            tag, msg = label_for(score)
            horizons[h] = {
                "score": round(score, 1),
                "signal": tag, "message": msg,
                "components": {"momentum": round(comp["momentum"], 1),
                               "seasonality": round(comp["seasonality"], 1),
                               "range": round(comp["range"], 1)},
                "momentum_detail": comp["momentum_detail"],
                "range_detail": comp["range_detail"],
                "seasonality_detail": comp["seasonality_detail"],
                "confidence": confidence_for(comp["momentum"],
                                             comp["seasonality"],
                                             comp["range"], vol),
            }
        ctx = GST_CONTEXT.get(key, {})
        if PUBLIC_BUILD:
            # public site: pounds only — no sales, margin, or cost exposure
            ctx_out = {"lbs": ctx["lbs"]} if ctx.get("lbs") else {}
            exposure = None
        else:
            ctx_out = ctx
            exposure = (round(ctx["lbs"] * ctx["cost_lb"] * 0.05)
                        if ctx.get("lbs") and ctx.get("cost_lb") else None)
        recent = pts[-PUBLISH_POINTS:]
        results[key] = {
            "name": prod["name"], "unit": prod["unit"],
            "spec": prod["spec"], "usda_item": prod["item"],
            "grade_section": prod["section"],
            "gst": ctx_out, "exposure_5pct": exposure,
            "current": round(prices[-1], 2),
            "last_market_date": pts[-1][0].isoformat(),
            "change_1d": round(prices[-1] - prices[-2], 2)
                if len(prices) > 1 else 0,
            "change_30d_pct": _calendar_change_pct(pts, 30),
            "volatility_ann_pct": round(vol, 1),
            "horizons": horizons,
            "series": [[d.isoformat(), round(p, 2)] for d, p in recent],
        }
    return results


def _business_days_between(a, b):
    days, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


# ---------------------------------------------------------------------------
# RENDER
# ---------------------------------------------------------------------------
def build(series, cutout, is_demo, warnings):
    analysis = analyze(series)
    last_dates = [pts[-1][0] for pts in series.values() if pts]
    last_market = max(last_dates) if last_dates else None
    today = dt.date.today()
    if last_market and _business_days_between(last_market, today) \
            > STALE_BUSINESS_DAYS:
        warnings.append(f"Newest USDA market date is {last_market.isoformat()}"
                        f" — data may be stale (holiday or feed problem).")
    missing = [p["name"] for p in PRODUCTS
               if p["key"] not in analysis]
    if missing and not is_demo:
        warnings.append("No usable series for: " + ", ".join(missing))
    cut_meta = None
    if cutout:
        d, ch, se = cutout[-1]
        prev = cutout[-2] if len(cutout) > 1 else None
        cut_meta = {"date": d.isoformat(), "choice": ch, "select": se,
                    "choice_chg_1d": round(ch - prev[1], 2) if prev else None}
    meta = {
        "generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "last_market_date": last_market.isoformat() if last_market else None,
        "is_demo": is_demo, "public": PUBLIC_BUILD,
        "warnings": warnings,
        "cutout": cut_meta,
        "source": "USDA AMS Market News — LM_XB403 (National Daily Boxed "
                  "Beef Cutout & Cuts), LMR DataMart",
        "weights": WEIGHTS,
        "thresholds": {"lock": LOCK_THRESHOLD, "hold": HOLD_THRESHOLD},
    }
    out = {"meta": meta, "products": analysis}
    here = os.path.dirname(__file__) or "."
    with open(os.path.join(here, "data.json"), "w") as f:
        json.dump(out, f, indent=2)
    with open(os.path.join(here, "index.html"), "w") as f:
        f.write(render_html(out))
    return out


def render_html(out):
    from dashboard_template import HTML
    payload = json.dumps(out).replace("</", "<\\/")   # </script> safety
    return HTML.replace("/*__DATA__*/", "window.APP_DATA = " + payload + ";")


# ---------------------------------------------------------------------------
def main():
    warnings = []
    if FORCE_DEMO:
        print("FORCE_DEMO set — DEMO mode (sample data).")
        (series, cutout), is_demo = generate_demo(), True
    else:
        try:
            rows, warnings = fetch_live()
            series, cutout = parse_rows(rows)
            good = sum(1 for v in series.values() if len(v) >= 90)
            if good == 0:
                warnings.append("Live pull returned no usable series — "
                                "check PRODUCTS mapping. Showing DEMO data.")
                print("WARNING:", warnings[-1])
                (series, cutout), is_demo = generate_demo(), True
            else:
                is_demo = False
                print(f"LIVE mode: {good}/{len(PRODUCTS)} product series.")
        except Exception as e:                        # noqa: BLE001
            warnings.append(f"Live fetch failed ({e}). Showing DEMO data.")
            print("WARNING:", warnings[-1])
            (series, cutout), is_demo = generate_demo(), True

    out = build(series, cutout, is_demo, warnings)
    print(f"Built dashboard for {len(out['products'])} products. "
          f"Mode: {'DEMO' if is_demo else 'LIVE'}.")
    if warnings:
        print("Warnings:", *warnings, sep="\n  - ")


if __name__ == "__main__":
    main()
