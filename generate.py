#!/usr/bin/env python3
"""
GST Meat Co. — Forward-Buy Signal Tool
=======================================
Pulls USDA boxed beef cutout + selected subprimal cut prices from the USDA
Market News (MARS) public API, analyzes trend / seasonality / range, and
produces a "Lock Score" for 30- and 60-day forward-buy decisions.

Output: data.json (raw computed series + signals) and index.html (dashboard).

Modes
-----
LIVE  : set env var USDA_API_KEY. Runs against the real MARS API.
DEMO  : no key present -> generates realistic synthetic data so the dashboard
        can be viewed/tested. Every screen is clearly flagged as SAMPLE DATA.

The signal math is IDENTICAL in both modes — only the data source changes.

Honesty note
------------
This tool does NOT predict prices. It measures whether the market is currently
under upward or downward pressure and how the calendar historically behaves,
then translates that into a lean. Treat it as decision support for a Cargill /
Zant lock conversation, not a forecast.
"""

import os
import json
import math
import base64
import datetime as dt
from urllib import request, parse, error

# ---------------------------------------------------------------------------
# CONFIG — what we track
# ---------------------------------------------------------------------------
# MARS report 2453 = LM_XB403, National Daily Boxed Beef Cutout & Cuts (PM).
# Each PRODUCT below is matched against report line items by label substrings.
# Field names in the live API vary slightly by report; MATCH is a list of
# lowercase substrings, any of which identifies the row. Adjust after first
# live pull if needed (see README "Confirming field names").
MARS_REPORT_ID = "2453"   # LMR DataMart slug id for LM_XB403 (boxed beef PM)

# GST's top 5 beef products (by sales, grouped by name across all codes),
# each mapped to the closest USDA boxed-beef item. MATCH = lowercase substrings
# that must ALL appear in a report line's label to identify the row.
PRODUCTS = [
    # key,           display name,                    unit,    match substrings
    ("chuck_roll",   "Diesmillo — Chuck Roll",        "$/cwt", ["chuck", "roll"]),
    ("flap",        "Fajita de Res — Flap (Bavette)","$/cwt", ["flap"]),
    ("shoulder_clod","Espaldia — Shoulder Clod",      "$/cwt", ["clod"]),
    ("short_rib",    "Costilla — Short Rib",          "$/cwt", ["short", "rib"]),
    ("round",        "Milanesa — Top/Inside Round",   "$/cwt", ["round"]),
]

# GST annual POUNDS per product (grouped by name). Pounds only here so this
# file is safe to keep in a public repo. The confidential sales/margin/cost
# figures live in gst_private.py (gitignored, never uploaded); if that file is
# present locally it fills in the dollar detail for the full local dashboard.
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

# How many calendar days of history to request/keep.
HISTORY_DAYS = 400

# Signal component weights (must sum to 1.0)
WEIGHTS = {"momentum": 0.40, "seasonality": 0.30, "range": 0.30}

API_BASE = "https://mpr.datamart.ams.usda.gov/services/v1.1/reports/"  # LMR DataMart

# When set (e.g. in GitHub Actions), the published build omits GST's
# confidential dollar figures/margins — pounds only. Local runs show full detail.
PUBLIC_BUILD = bool(os.environ.get("PUBLIC_BUILD"))


# ---------------------------------------------------------------------------
# DATA FETCH — live
# ---------------------------------------------------------------------------
def _extract_rows(payload):
    """Normalize a MARS/DataMart payload into a list of dict rows."""
    if isinstance(payload, dict):
        for k in ("results", "Results", "report", "data"):
            v = payload.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        # dict of sections -> flatten lists of dicts
        rows = []
        for v in payload.values():
            if isinstance(v, list):
                rows.extend([x for x in v if isinstance(x, dict)])
        return rows
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def fetch_live(api_key):
    """Pull boxed-beef rows from the LMR DataMart. Tries a few URL/auth
    combinations and logs raw response heads so the exact shape is visible."""
    end = dt.date.today()
    begin = end - dt.timedelta(days=HISTORY_DAYS)
    base = API_BASE + MARS_REPORT_ID
    dr = f"{begin:%m/%d/%Y}:{end:%m/%d/%Y}"
    url_candidates = [
        base,                                              # latest snapshot
        base + "?q=report_begin_date=" + parse.quote(dr, safe="/:"),
        base + "/Current+Cutout+Values",                   # a named section
    ]
    for url in url_candidates:
        for use_auth in (False, True):
            headers = {}
            if use_auth and api_key:
                headers["Authorization"] = ("Basic " +
                    base64.b64encode(f"{api_key}:".encode()).decode())
            try:
                req = request.Request(url, headers=headers)
                with request.urlopen(req, timeout=90) as r:
                    raw = r.read().decode()
                print(f"TRY {url[:95]} auth={use_auth} -> {len(raw)}b "
                      f"head={raw[:200]!r}")
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                rows = _extract_rows(payload)
                if rows:
                    print(f"  -> extracted {len(rows)} dict rows; "
                          f"keys={list(rows[0].keys())}")
                    return rows
            except error.HTTPError as e:
                print(f"TRY {url[:95]} auth={use_auth} -> HTTP {e.code}")
                continue
            except Exception as ex:
                print(f"TRY {url[:95]} auth={use_auth} -> ERR {ex}")
                continue
    return []


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def parse_live_records(records):
    """Turn raw MARS rows into {product_key: [(date, price), ...]}.

    Strategy: for each row, find a date field and a price field, and a label
    field, then bucket into the first PRODUCT whose MATCH substrings all appear
    in the label. Kept deliberately tolerant of field-name variation.
    """
    series = {k: {} for (k, *_ ) in PRODUCTS}
    if isinstance(records, dict):
        records = records.get("results") or list(records.values())
    for row in records:
        if not isinstance(row, dict):
            continue
        low = {str(k).lower(): v for k, v in row.items()}
        # date
        date = None
        for cand in ("report_date", "reportdate", "date"):
            if cand in low and low[cand]:
                try:
                    date = dt.datetime.strptime(str(low[cand])[:10],
                                                "%m/%d/%Y").date()
                except ValueError:
                    try:
                        date = dt.datetime.strptime(str(low[cand])[:10],
                                                    "%Y-%m-%d").date()
                    except ValueError:
                        date = None
                if date:
                    break
        if not date:
            continue
        # label (what item is this row)
        label = " ".join(str(low.get(f, "")) for f in
                         ("item_description", "commodity", "cut", "report_title",
                          "description", "primal")).lower()
        # price
        price = None
        for cand in ("current_cutout_value", "weighted_average", "wtd_avg",
                     "avg_price", "weighted_avg_price", "price"):
            if cand in low:
                price = _num(low[cand])
                if price:
                    break
        if price is None:
            continue
        for (key, _name, _unit, match) in PRODUCTS:
            if all(m in label for m in match):
                # keep last price seen per date (PM report is cumulative)
                series[key][date] = price
                break
    # to sorted lists
    out = {}
    for key, dmap in series.items():
        out[key] = sorted(dmap.items())
    return out


# ---------------------------------------------------------------------------
# DATA FETCH — demo (synthetic but realistic)
# ---------------------------------------------------------------------------
def generate_demo():
    """Realistic synthetic daily series so the dashboard is viewable offline.

    Beef cutout has a strong spring/early-summer grilling peak and a winter
    trough, plus a slow multi-year uptrend and daily noise. Values are in the
    right ballpark ($/cwt) but are NOT real market data.
    """
    import random
    random.seed(42)
    end = dt.date.today()
    dates = [end - dt.timedelta(days=i) for i in range(HISTORY_DAYS)][::-1]

    def seasonal(d):
        # peak ~ late May (day-of-year ~145), trough ~ late Dec
        doy = d.timetuple().tm_yday
        return math.sin((doy - 55) / 365 * 2 * math.pi)

    # anchored near GST's implied wholesale cost/lb (×100) so demo looks sane
    base_levels = {
        "chuck_roll": 495, "flap": 802, "shoulder_clod": 425,
        "short_rib": 544, "round": 487,
    }
    amp = {  # seasonal swing amplitude $/cwt (flap & short rib swing hardest)
        "chuck_roll": 35, "flap": 85, "shoulder_clod": 22,
        "short_rib": 55, "round": 28,
    }
    out = {}
    for key, base in base_levels.items():
        level = base
        vals = []
        for i, d in enumerate(dates):
            trend = i * 0.03  # slow uptrend
            seas = amp[key] * seasonal(d)
            level += random.gauss(0, 1.4)      # random walk component
            level = 0.995 * level + 0.005 * base  # gentle mean reversion
            price = level + trend + seas + random.gauss(0, 2.0)
            vals.append((d, round(price, 2)))
        out[key] = vals
    return out


# ---------------------------------------------------------------------------
# SIGNAL MATH
# ---------------------------------------------------------------------------
def _sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def momentum_score(prices):
    """0-100. >50 => upward pressure (favors locking)."""
    if len(prices) < 45:
        return 50.0, {}
    short = _sma(prices, 10)
    long = _sma(prices, 40)
    # % spread of short vs long MA; +2% spread -> strong up
    spread = (short - long) / long * 100 if long else 0
    # 20-day rate of change
    roc = (prices[-1] - prices[-21]) / prices[-21] * 100 if prices[-21] else 0
    raw = 50 + spread * 6 + roc * 1.5
    return _clamp(raw), {"ma_spread_pct": round(spread, 2),
                         "roc_20d_pct": round(roc, 2),
                         "sma10": round(short, 2), "sma40": round(long, 2)}


def seasonality_score(series, horizon_days):
    """0-100 based on how this calendar window has historically moved.

    Compares the average price in the upcoming N-day window (across the years
    of history we have) to the trailing N-day window at the same point.
    >50 => calendar historically rises into this window.
    """
    if len(series) < 120:
        return 50.0, {}
    by_doy = {}
    for d, p in series:
        by_doy.setdefault(d.timetuple().tm_yday, []).append(p)
    today_doy = dt.date.today().timetuple().tm_yday

    def window_avg(center, width=15):
        acc = []
        for off in range(-width, width + 1):
            doy = ((center + off - 1) % 365) + 1
            if doy in by_doy:
                acc.extend(by_doy[doy])
        return sum(acc) / len(acc) if acc else None

    now = window_avg(today_doy)
    ahead = window_avg((today_doy + horizon_days - 1) % 365 + 1)
    if not now or not ahead:
        return 50.0, {}
    chg = (ahead - now) / now * 100
    raw = 50 + chg * 5
    return _clamp(raw), {"seasonal_chg_pct": round(chg, 2)}


def range_score(prices, lookback=120):
    """0-100. Where does today's price sit in its recent range?

    Low in range => more room to rise => higher lock score.
    High in range => less benefit locking at a peak => lower lock score.
    """
    window = prices[-lookback:] if len(prices) >= lookback else prices
    lo, hi = min(window), max(window)
    if hi == lo:
        return 50.0, {}
    pct = (prices[-1] - lo) / (hi - lo)  # 0 = at low, 1 = at high
    raw = (1 - pct) * 100
    return _clamp(raw), {"range_pctile": round(pct * 100, 1),
                         "low": round(lo, 2), "high": round(hi, 2)}


def volatility(prices, n=30):
    if len(prices) < n + 1:
        return 0.0
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(len(prices) - n, len(prices)) if prices[i - 1]]
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(252) * 100  # annualized %, rough


def label_for(score):
    if score >= 62:
        return ("LOCK", "Upward pressure — locking looks favorable")
    if score >= 45:
        return ("SPLIT", "Mixed signals — consider locking part of the volume")
    return ("HOLD", "Soft / downward — little urgency to lock now")


def confidence_for(mom, seas, rng, vol):
    """Low/Medium/High based on agreement of components and volatility."""
    scores = [mom, seas, rng]
    spread = max(scores) - min(scores)  # disagreement
    # all pointing same side of 50?
    same_side = all(s >= 50 for s in scores) or all(s < 50 for s in scores)
    if same_side and spread < 25 and vol < 25:
        return "High"
    if spread < 40 and vol < 40:
        return "Medium"
    return "Low"


def analyze(series):
    results = {}
    for (key, name, unit, _match) in PRODUCTS:
        pts = series.get(key, [])
        if len(pts) < 45:
            continue
        prices = [p for _d, p in pts]
        mom, mom_d = momentum_score(prices)
        rng, rng_d = range_score(prices)
        vol = volatility(prices)
        horizons = {}
        for h in (30, 60):
            seas, seas_d = seasonality_score(pts, h)
            score = (WEIGHTS["momentum"] * mom +
                     WEIGHTS["seasonality"] * seas +
                     WEIGHTS["range"] * rng)
            tag, msg = label_for(score)
            horizons[h] = {
                "score": round(score, 1),
                "signal": tag, "message": msg,
                "components": {"momentum": round(mom, 1),
                               "seasonality": round(seas, 1),
                               "range": round(rng, 1)},
                "seasonality_detail": seas_d,
                "confidence": confidence_for(mom, seas, rng, vol),
            }
        ctx = GST_CONTEXT.get(key, {})
        if PUBLIC_BUILD:
            # public site: pounds only — no sales, margin, or cost exposure
            ctx_out = {"lbs": ctx["lbs"]} if ctx else {}
            exposure = None
        else:
            ctx_out = ctx
            exposure = round(ctx["lbs"] * ctx["cost_lb"] * 0.05) if ctx.get("cost_lb") else None
        results[key] = {
            "name": name, "unit": unit,
            "gst": ctx_out, "exposure_5pct": exposure,
            "current": prices[-1],
            "change_1d": round(prices[-1] - prices[-2], 2) if len(prices) > 1 else 0,
            "change_30d_pct": round((prices[-1] - prices[-31]) / prices[-31] * 100, 1)
                if len(prices) > 31 and prices[-31] else 0,
            "volatility_ann_pct": round(vol, 1),
            "momentum_detail": mom_d, "range_detail": rng_d,
            "horizons": horizons,
            "series": [[d.isoformat(), p] for d, p in pts],
        }
    return results


# ---------------------------------------------------------------------------
# RENDER
# ---------------------------------------------------------------------------
def build(series, is_demo):
    analysis = analyze(series)
    meta = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "is_demo": is_demo, "public": PUBLIC_BUILD,
        "source": "USDA AMS Market News — LM_XB403 (National Daily Boxed Beef "
                  "Cutout & Cuts)",
        "weights": WEIGHTS,
    }
    out = {"meta": meta, "products": analysis}
    here = os.path.dirname(__file__) or "."
    with open(os.path.join(here, "data.json"), "w") as f:
        json.dump(out, f, indent=2)
    html = render_html(out)
    with open(os.path.join(here, "index.html"), "w") as f:
        f.write(html)
    return out


def render_html(out):
    # index.html is written by dashboard_template.py to keep this file focused.
    from dashboard_template import HTML
    return HTML.replace("/*__DATA__*/",
                        "window.APP_DATA = " + json.dumps(out) + ";")


# ---------------------------------------------------------------------------
def main():
    key = os.environ.get("USDA_API_KEY", "").strip()
    if key:
        try:
            records = fetch_live(key)
            _n = len(records) if hasattr(records, "__len__") else "?"
            print(f"LIVE fetch: {_n} raw records; type={type(records).__name__}")
            if isinstance(records, dict):
                print("RESULTS-DICT KEYS:", list(records.keys())[:20])
                _first = next(iter(records.values())) if records else None
                print("  first value type:", type(_first).__name__,
                      "repr:", repr(_first)[:300])
            elif records:
                e0 = records[0]
                print("elem0 type:", type(e0).__name__, "repr:", repr(e0)[:400])
                if isinstance(e0, dict):
                    print("RECORD KEYS:", list(e0.keys()))
                    for _r in records[:4]:
                        print("SAMPLE ROW:", _r)
            series = parse_live_records(records)
            for _k, _v in series.items():
                print(f"  mapped {_k}: {len(_v)} points")
            # If parsing produced too-thin series, fall back to demo but warn.
            good = sum(1 for v in series.values() if len(v) >= 45)
            if good == 0:
                print("WARNING: live pull returned no usable series — check "
                      "field mapping (see README). Falling back to DEMO.")
                series, is_demo = generate_demo(), True
            else:
                is_demo = False
                print(f"LIVE mode: {good} product series pulled.")
        except (error.URLError, error.HTTPError, ValueError) as e:
            print(f"WARNING: live fetch failed ({e}). Diagnosing...")
            sid = diagnose_and_resolve(key)
            if sid:
                try:
                    globals()["MARS_REPORT_ID"] = str(sid)
                    print(f"Retrying with resolved slug_id={sid} ...")
                    records = fetch_live(key)
                    print(f"LIVE fetch (retry): {len(records)} records.")
                    if records:
                        _keys = list(records[0].keys())
                        print("RECORD KEYS:", _keys)
                        for _r in records[:4]:
                            print("SAMPLE ROW:", {k: _r.get(k) for k in _keys})
                    series = parse_live_records(records)
                    for _k, _v in series.items():
                        print(f"  mapped {_k}: {len(_v)} points")
                    good = sum(1 for v in series.values() if len(v) >= 45)
                    is_demo = good == 0
                except Exception as e2:
                    print(f"retry failed: {e2}")
                    series, is_demo = generate_demo(), True
            else:
                series, is_demo = generate_demo(), True
    else:
        print("No USDA_API_KEY set — DEMO mode (sample data).")
        series, is_demo = generate_demo(), True

    out = build(series, is_demo)
    n = len(out["products"])
    print(f"Built dashboard for {n} products. Mode: "
          f"{'DEMO' if is_demo else 'LIVE'}.")


if __name__ == "__main__":
    main()
