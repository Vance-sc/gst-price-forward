#!/usr/bin/env python3
"""
GST Meat Co. — Forward-Buy Signal Tool (v2 signal engine)
=========================================================
Pulls USDA boxed-beef cut prices from the LMR DataMart (report LM_XB403,
slug 2453), computes the v2 Lock Score, and writes data.json + index.html.

v2 signal (validated out-of-sample, see VALIDATION below)
---------------------------------------------------------
Score = 35% relative value + 22% contrarian momentum + 18% volume trend
        + 13% Choice/Select-spread anomaly + 12% calendar seasonality.

  * Relative value : cut price / Choice cutout vs its own trailing 250-obs
                     average ratio. CHEAP vs cutout -> high score.
  * Momentum       : 21-obs (30d) / 42-obs (60d) rate of change, CONTRARIAN —
                     these cuts mean-revert, so falling -> high score.
  * Volume trend   : 20-obs avg pounds traded vs 250-obs avg. Heavy
                     negotiated volume -> high score.
  * C/S anomaly    : Choice-Select cutout spread vs trailing mean. Wide ->
                     low score.
  * Seasonality    : cut/cutout ratio vs same ISO-week mean over prior years
                     only (no lookahead). Cheap-for-this-week -> high score.

Each feature is z-scored against its own trailing 250 observations
(self-calibrating; no fitted constants except weights and thresholds), then
squashed with tanh. The v1 momentum-positive score backtested INVERTED —
do not restore it.

Provenance: features ranked by quintile-spread study on 2019-2026 history;
weights calibrated on 2019-2023 and validated out-of-sample (walk-forward,
no lookahead). LOCK cutoffs use a hardened self-calibrating percentile
(asymmetric: stricter on 30d) so LOCK is rarer and closer to Vance's
~4–5% high-confidence lock bar; soft SPLIT must not nudge locking.
Per-bucket hit rates in VALIDATION are shown as the confidence figure.

Data source
-----------
LMR DataMart (keyless):
  https://mpr.datamart.ams.usda.gov/services/v1.1/reports/2453/<Section>
Date filter: ?q=report_date=MM/DD/YYYY:MM/DD/YYYY  (column is report_date).
ALWAYS use the date filter — unfiltered pulls cap at 100,000 rows silently.
No-trade rows publish weighted_average ".00" and are NOT prices: discard.
Each product pins to ONE exact item_description + grade section.

Modes: LIVE by default; FORCE_DEMO=1 or fetch failure -> flagged demo data.

This tool is decision support for a vendor lock conversation, not a forecast.
"""

import os
import json
import math
import datetime as dt
from urllib import request, parse, error

# ---------------------------------------------------------------------------
# CONFIG — what we track
# ---------------------------------------------------------------------------
REPORT_ID = "2453"
API_BASE = "https://mpr.datamart.ams.usda.gov/services/v1.1/reports/"

# One exact USDA item + grade per product (strings verbatim from the API —
# note the double spaces). Swap the whole string to remap a product.
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
    # Costilla = CHUCK short rib (130). Verified against GST invoices
    # 2026-Q2: Zant sells GST "BEEF CHUCK SHORT RIBS 5-BONE" and Cargill
    # "CHK B/I SHTRB"; paid prices correlate with USDA 130 and NEGATIVELY
    # with plate 123A. Do not switch back to 123A without invoice evidence.
    {"key": "short_rib",
     "name": "Costilla — Chuck Short Rib (5-Bone)",
     "unit": "$/cwt",
     "item": "Chuck, short rib (130  4)",
     "section": "Choice Cuts",
     "spec": "USDA 130 · Choice"},
    {"key": "round",
     "name": "Milanesa — Knuckle (Peeled)",
     "unit": "$/cwt",
     "item": "Round, knuckle, peeled (167A  4)",
     "section": "Choice Cuts",
     "spec": "USDA 167A · Choice"},
]

CUTOUT_SECTION = "Current Cutout Values"

# GST annual POUNDS per product (safe for a public repo). Confidential
# sales/margin/cost figures live in gst_private.py (gitignored).
GST_CONTEXT = {
    "chuck_roll":    {"lbs": 511841},
    "flap":          {"lbs": 200510},
    "shoulder_clod": {"lbs": 74061},
    "short_rib":     {"lbs": 56158},
    "round":         {"lbs": 35926},
}
try:
    from gst_private import GST_CONTEXT_FULL
    for _k, _v in GST_CONTEXT_FULL.items():
        GST_CONTEXT.setdefault(_k, {}).update(_v)
except ImportError:
    pass

FETCH_YEARS = 5          # features need ~500 obs of warmup; 5y ≈ 1,250

# v2+seasonality weights — sum to 1.0. Seasonality ~12% for same-ISO-week
# cheap/rich vs history; other weights trimmed proportionally from v2.
WEIGHTS = {"rel_value": 0.35, "momentum": 0.22,
           "volume": 0.18, "cs_spread": 0.13, "seasonality": 0.12}

# Thresholds are SELF-CALIBRATING at build time from pooled decision-day
# scores (see pooled_thresholds). Mirrors expanding-window validation.
# Vance lock bar: only lock with vendors when expected upside is ~4–5%
# with pretty high confidence; otherwise float. Soft SPLIT must not nudge
# locking. LOCK uses a higher percentile than HOLD, and 30d is stricter
# than 60d (shorter horizon needs a rarer/top-tier score for similar edge).
LOCK_PERCENTILE = {30: 0.88, 60: 0.82}
HOLD_PERCENTILE = 0.30
# Keep LOCK only when validated OOS mean/hit meet the bar; else downgrade
# to SPLIT (float language). Applied after score labeling in analyze().
LOCK_MIN_MEAN = {30: 3.5, 60: 4.0}   # ~4–5% bar; 30d floor = hardened pooled edge
LOCK_MIN_HIT = 0.65                 # High confidence (same cutoff as conf label)
# Static fallback only if history is too thin (raised with the harder bar).
DEFAULT_THRESHOLDS = {30: {"lock": 68.0, "hold": 40.0},
                      60: {"lock": 66.0, "hold": 39.5}}

MSG_LOCK = "Top-tier Lock Score — locking looks favorable vs floating"
MSG_SPLIT = "Mixed — prefer floating / waiting; no strong lock case yet"
MSG_HOLD = "Rich vs cutout / post-rally — wait"

# Out-of-sample validation: EXPANDING-WINDOW walk-forward (thresholds use
# LOCK_PERCENTILE / HOLD_PERCENTILE on prior pooled scores only; decisions
# every 5 trading days). Edge held across eras. mean = avg forward price
# move on days in that bucket; hit = share of days the price rose (for HOLD,
# a LOW hit rate is good — you were waiting). Regenerate via backtest.py
# after any model change. Numbers below are percentile-policy OOS; live
# LOCK is further gated by apply_lock_bar() (Vance ~4–5% / High conf bar).
VALIDATION = {
 'pooled': {
  30: {'LOCK': {'mean': 3.51, 'hit': 0.66, 'n': 200}, 'SPLIT': {'mean': 1.14, 'hit': 0.57, 'n': 1088}, 'HOLD': {'mean': -0.56, 'hit': 0.43, 'n': 564}},
  60: {'LOCK': {'mean': 4.92, 'hit': 0.7, 'n': 298}, 'SPLIT': {'mean': 2.54, 'hit': 0.59, 'n': 976}, 'HOLD': {'mean': -1.78, 'hit': 0.4, 'n': 557}}
 },
 'chuck_roll': {
  30: {'LOCK': {'mean': 1.76, 'hit': 0.64, 'n': 39}, 'SPLIT': {'mean': 1.77, 'hit': 0.57, 'n': 221}, 'HOLD': {'mean': -0.76, 'hit': 0.43, 'n': 111}},
  60: {'LOCK': {'mean': 3.54, 'hit': 0.6, 'n': 67}, 'SPLIT': {'mean': 4.38, 'hit': 0.63, 'n': 187}, 'HOLD': {'mean': -3.02, 'hit': 0.35, 'n': 113}}
 },
 'flap': {
  30: {'LOCK': {'mean': 6.57, 'hit': 0.7, 'n': 47}, 'SPLIT': {'mean': 0.38, 'hit': 0.53, 'n': 204}, 'HOLD': {'mean': -0.45, 'hit': 0.49, 'n': 118}},
  60: {'LOCK': {'mean': 6.66, 'hit': 0.75, 'n': 65}, 'SPLIT': {'mean': 2.16, 'hit': 0.6, 'n': 182}, 'HOLD': {'mean': -2.06, 'hit': 0.42, 'n': 118}}
 },
 'shoulder_clod': {
  30: {'LOCK': {'mean': 1.95, 'hit': 0.61, 'n': 38}, 'SPLIT': {'mean': 1.64, 'hit': 0.59, 'n': 209}, 'HOLD': {'mean': -0.4, 'hit': 0.38, 'n': 124}},
  60: {'LOCK': {'mean': 3.12, 'hit': 0.63, 'n': 62}, 'SPLIT': {'mean': 3.06, 'hit': 0.62, 'n': 181}, 'HOLD': {'mean': -0.67, 'hit': 0.41, 'n': 123}}
 },
 'short_rib': {
  30: {'LOCK': {'mean': 2.21, 'hit': 0.53, 'n': 38}, 'SPLIT': {'mean': 0.69, 'hit': 0.53, 'n': 228}, 'HOLD': {'mean': -0.51, 'hit': 0.42, 'n': 104}},
  60: {'LOCK': {'mean': 3.43, 'hit': 0.62, 'n': 53}, 'SPLIT': {'mean': 1.09, 'hit': 0.54, 'n': 212}, 'HOLD': {'mean': -0.76, 'hit': 0.43, 'n': 101}}
 },
 'round': {
  30: {'LOCK': {'mean': 4.38, 'hit': 0.79, 'n': 38}, 'SPLIT': {'mean': 1.22, 'hit': 0.61, 'n': 226}, 'HOLD': {'mean': -0.68, 'hit': 0.41, 'n': 107}},
  60: {'LOCK': {'mean': 8.25, 'hit': 0.96, 'n': 51}, 'SPLIT': {'mean': 2.26, 'hit': 0.57, 'n': 214}, 'HOLD': {'mean': -2.41, 'hit': 0.38, 'n': 102}}
 }
}
PUBLISH_POINTS = 270
STALE_BUSINESS_DAYS = 4
PUBLIC_BUILD = bool(os.environ.get("PUBLIC_BUILD"))
FORCE_DEMO = bool(os.environ.get("FORCE_DEMO"))


# ---------------------------------------------------------------------------
# DATA FETCH
# ---------------------------------------------------------------------------
def _extract_rows(payload):
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
    """-> (series, cutout)
    series = {key: [(date, price, lbs), ...]}   volume-weighted, sorted
    cutout = [(date, choice, select_or_None), ...] sorted

    Hygiene: exact item+section match; ".00"/null weighted_average and
    zero/blank total_pounds rows are no-trade rows -> discarded."""
    want = {(p["item"], p["section"]): p["key"] for p in PRODUCTS}
    acc = {p["key"]: {} for p in PRODUCTS}
    cut = {}
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
        if price is None or price <= 0 or lbs is None or lbs <= 0:
            continue
        acc[key].setdefault(date, []).append((price, lbs))
    series = {}
    for key, dmap in acc.items():
        pts = []
        for d, entries in dmap.items():
            wsum = sum(l for _p, l in entries)
            pts.append((d, sum(p * l for p, l in entries) / wsum, wsum))
        series[key] = sorted(pts)
    cutout = sorted((d, c, s) for d, (c, s) in cut.items())
    print("mapped points:", {k: len(v) for k, v in series.items()})
    return series, cutout


# ---------------------------------------------------------------------------
# DEMO DATA (synthetic; generic public USDA ballparks, not GST figures)
# ---------------------------------------------------------------------------
def generate_demo():
    import random
    random.seed(42)
    end = dt.date.today()
    days = int(FETCH_YEARS * 365.25)
    dates = [end - dt.timedelta(days=i) for i in range(days)][::-1]
    dates = [d for d in dates if d.weekday() < 5]

    def seasonal(d):
        doy = d.timetuple().tm_yday
        return math.sin((doy - 55) / 365 * 2 * math.pi)

    base_levels = {"chuck_roll": 480, "flap": 950, "shoulder_clod": 440,
                   "short_rib": 515, "round": 465}
    base_lbs = {"chuck_roll": 300000, "flap": 35000, "shoulder_clod": 90000,
                "short_rib": 30000, "round": 150000}
    amp = {"chuck_roll": 35, "flap": 85, "shoulder_clod": 22,
           "short_rib": 55, "round": 28}
    out = {}
    for key, base in base_levels.items():
        level = base * 0.7
        vals = []
        for i, d in enumerate(dates):
            trend = i * (base * 0.3 / len(dates))
            seas = amp[key] * seasonal(d)
            level += random.gauss(0, 1.4)
            level = 0.995 * level + 0.005 * base * 0.7
            lbs = max(1000, base_lbs[key] * (1 + random.gauss(0, 0.35)))
            vals.append((d, round(level + trend + seas
                                  + random.gauss(0, 2.0), 2), round(lbs)))
        out[key] = vals
    cutout = []
    for i, d in enumerate(dates):
        ch = 300 + i * 0.06 + 25 * seasonal(d) + random.gauss(0, 2)
        cutout.append((d, round(ch, 2), round(ch - 18 + random.gauss(0, 3), 2)))
    return out, cutout


# ---------------------------------------------------------------------------
# v2 SIGNAL ENGINE
# ---------------------------------------------------------------------------
def _mean(v):
    return sum(v) / len(v)


def _sd(v):
    m = _mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1.0


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _comp(z, sign):
    """z-score -> 0-100 component. sign=-1 for contrarian features."""
    return 50 + 50 * math.tanh(sign * z / 1.5)


def build_features(series, cutout):
    """Daily raw feature arrays for one product (None where undefined).
    series = [(date, price, lbs)], cutout = [(date, choice, select)]."""
    cut_dates = [c[0] for c in cutout]

    def cut_at(d):
        # index of last cutout entry <= d (binary search)
        import bisect
        i = bisect.bisect_right(cut_dates, d) - 1
        return i if i >= 0 else None

    n = len(series)
    ratio, cs = [], []
    for d, p, _l in series:
        ci = cut_at(d)
        ratio.append(p / cutout[ci][1] if ci is not None else None)
        cs.append(cutout[ci][1] - cutout[ci][2]
                  if ci is not None and cutout[ci][2] is not None else None)
    F = {"rv": [None] * n, "roc30": [None] * n, "roc60": [None] * n,
         "vr": [None] * n, "cs": [None] * n, "seas": [None] * n}
    prices = [p for _d, p, _l in series]
    lbs = [l for _d, _p, l in series]
    # Prior same-ISO-week ratios only (strictly earlier observations — no lookahead).
    week_hist = {}
    for i in range(n):
        if i >= 250 and ratio[i] is not None:
            w = [r for r in ratio[i - 250:i] if r is not None]
            if len(w) > 150:
                F["rv"][i] = (ratio[i] / _mean(w) - 1) * 100
        if i >= 21 and prices[i - 21]:
            F["roc30"][i] = (prices[i] - prices[i - 21]) / prices[i - 21] * 100
        if i >= 42 and prices[i - 42]:
            F["roc60"][i] = (prices[i] - prices[i - 42]) / prices[i - 42] * 100
        if i >= 250:
            v20 = _mean(lbs[i - 19:i + 1])
            v250 = _mean(lbs[i - 249:i + 1])
            if v250:
                F["vr"][i] = (v20 / v250 - 1) * 100
        if i >= 250 and cs[i] is not None:
            w = [x for x in cs[i - 250:i] if x is not None]
            if len(w) > 100:
                F["cs"][i] = cs[i] - _mean(w)
        # Seasonality: cheap/rich for this ISO week vs prior same weeks only.
        d = series[i][0]
        iso_w = d.isocalendar()[1]
        prior = week_hist.get(iso_w, [])
        if ratio[i] is not None and len(prior) >= 3:
            sm = _mean(prior)
            if sm:
                F["seas"][i] = (ratio[i] / sm - 1) * 100
        if ratio[i] is not None:
            week_hist.setdefault(iso_w, []).append(ratio[i])
    return F


def _z_at(arr, i):
    if arr[i] is None:
        return None
    w = [x for x in arr[max(0, i - 250):i] if x is not None]
    if len(w) < 100:
        return None
    return (arr[i] - _mean(w)) / _sd(w)


def score_at(F, i, horizon_days):
    """v2+seasonality composite at index i, or (None, {}) if warmup insufficient.
    Signs: rel-value / momentum / C/S / seasonality contrarian (cheap=high);
    volume positive. Seasonality optional (neutral 50 if thin history)."""
    zr = _z_at(F["rv"], i)
    zm = _z_at(F["roc30" if horizon_days <= 30 else "roc60"], i)
    zv = _z_at(F["vr"], i)
    zc = _z_at(F["cs"], i)
    zs = _z_at(F["seas"], i) if "seas" in F else None
    if zr is None or zm is None or zv is None:
        return None, {}
    rv_c, m_c, v_c = _comp(zr, -1), _comp(zm, -1), _comp(zv, 1)
    cs_c = _comp(zc, -1) if zc is not None else 50.0
    seas_c = _comp(zs, -1) if zs is not None else 50.0
    score = (WEIGHTS["rel_value"] * rv_c + WEIGHTS["momentum"] * m_c
             + WEIGHTS["volume"] * v_c + WEIGHTS["cs_spread"] * cs_c
             + WEIGHTS["seasonality"] * seas_c)
    detail = {"rel_value": round(rv_c, 1), "momentum": round(m_c, 1),
              "volume": round(v_c, 1), "cs_spread": round(cs_c, 1),
              "seasonality": round(seas_c, 1),
              "z": {"rel_value": round(zr, 2), "momentum": round(zm, 2),
                    "volume": round(zv, 2),
                    "cs_spread": round(zc, 2) if zc is not None else None,
                    "seasonality": round(zs, 2) if zs is not None else None},
              "rv_pct": round(F["rv"][i], 2)}
    return _clamp(score), detail


def pooled_thresholds(feature_map):
    """Self-calibrating LOCK/HOLD cutoffs from pooled decision-day scores
    (every 5th obs after warmup) across all products — same procedure as
    expanding-window validation. LOCK percentile is asymmetric (stricter
    on 30d) to push LOCK edge toward Vance's ~4–5% high-confidence bar."""
    th = {}
    for h in (30, 60):
        scores = []
        for _key, F in feature_map.items():
            for i in range(520, len(F["rv"]), 5):
                s, _d = score_at(F, i, h)
                if s is not None:
                    scores.append(s)
        if len(scores) >= 150:
            scores.sort()
            lp = LOCK_PERCENTILE[h]
            hp = HOLD_PERCENTILE
            th[h] = {"lock": round(scores[int(len(scores) * lp)], 1),
                     "hold": round(scores[int(len(scores) * hp)], 1)}
        else:
            th[h] = dict(DEFAULT_THRESHOLDS[h])
    return th


def label_for(score, horizon_days, thresholds=None):
    """Score-only LOCK/SPLIT/HOLD. apply_lock_bar() may downgrade LOCK."""
    th = (thresholds or DEFAULT_THRESHOLDS)[30 if horizon_days <= 30 else 60]
    if score >= th["lock"]:
        return ("LOCK", MSG_LOCK)
    if score >= th["hold"]:
        return ("SPLIT", MSG_SPLIT)
    return ("HOLD", MSG_HOLD)


def validation_for(key, horizon_days, signal):
    """Out-of-sample stats for this product+horizon+bucket (pooled fallback)."""
    h = 30 if horizon_days <= 30 else 60
    v = VALIDATION.get(key, {}).get(h, {}).get(signal) \
        or VALIDATION["pooled"][h].get(signal)
    if not v:
        return None, "Low"
    # Confidence from validated hit rate. For HOLD, a LOW hit rate is good
    # (you wanted the price to fall while you waited).
    eff = (1 - v["hit"]) if signal == "HOLD" else \
        (v["hit"] if signal == "LOCK" else 0.5)
    conf = "High" if eff >= 0.65 else "Medium" if eff >= 0.55 else "Low"
    if v["n"] < 25 and conf == "High":
        conf = "Medium"
    return v, conf


def meets_lock_bar(vstats, conf, horizon_days):
    """Vance bar: ~4–5% expected upside with High confidence."""
    h = 30 if horizon_days <= 30 else 60
    if not vstats:
        return False
    if conf != "High":
        return False
    if vstats.get("hit", 0) < LOCK_MIN_HIT:
        return False
    if vstats.get("mean", 0) < LOCK_MIN_MEAN[h]:
        return False
    return True


def apply_lock_bar(tag, msg, key, horizon_days):
    """Downgrade score-based LOCK to SPLIT unless validated bar is met.
    Soft SPLIT must not nudge locking — float/wait language only."""
    if tag != "LOCK":
        return tag, msg, *validation_for(key, horizon_days, tag)
    vstats, conf = validation_for(key, horizon_days, "LOCK")
    if meets_lock_bar(vstats, conf, horizon_days):
        return "LOCK", MSG_LOCK, vstats, conf
    # Near-lock / soft lock -> float language (SPLIT), not a lock nudge
    v2, conf2 = validation_for(key, horizon_days, "SPLIT")
    return "SPLIT", MSG_SPLIT, v2, conf2


def volatility(prices, n=30):
    if len(prices) < n + 1:
        return 0.0
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(len(prices) - n, len(prices)) if prices[i - 1]]
    if not rets:
        return 0.0
    m = _mean(rets)
    var = sum((r - m) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(252) * 100


# ---------------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------------
def _calendar_change_pct(series, days):
    if len(series) < 2:
        return 0.0
    target = series[-1][0] - dt.timedelta(days=days)
    past = None
    for pt in reversed(series):
        if pt[0] <= target:
            past = pt[1]
            break
    if not past:
        return 0.0
    return round((series[-1][1] - past) / past * 100, 1)


def analyze(series, cutout):
    results = {}
    feature_map = {}
    for prod in PRODUCTS:
        pts = series.get(prod["key"], [])
        if len(pts) >= 400 and cutout:
            feature_map[prod["key"]] = build_features(pts, cutout)
    thresholds = pooled_thresholds(feature_map)
    for prod in PRODUCTS:
        key = prod["key"]
        pts = series.get(key, [])
        if key not in feature_map:
            continue
        F = feature_map[key]
        prices = [p for _d, p, _l in pts]
        vol = volatility(prices)
        horizons = {}
        usable = True
        for h in (30, 60):
            score, detail = score_at(F, len(pts) - 1, h)
            if score is None:
                usable = False
                break
            tag, msg = label_for(score, h, thresholds)
            tag, msg, vstats, conf = apply_lock_bar(tag, msg, key, h)
            horizons[h] = {
                "score": round(score, 1),
                "signal": tag, "message": msg,
                "components": {k: detail[k] for k in
                               ("rel_value", "momentum", "volume",
                                "cs_spread", "seasonality")},
                "detail": detail,
                "confidence": conf,
                "validation": vstats,
            }
        if not usable:
            continue
        ctx = GST_CONTEXT.get(key, {})
        if PUBLIC_BUILD:
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
            "rv_pct": horizons[30]["detail"]["rv_pct"],
            "horizons": horizons,
            "series": [[d.isoformat(), round(p, 2)] for d, p, _l in recent],
        }
    return results, thresholds


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
    analysis, thresholds = analyze(series, cutout)
    last_dates = [pts[-1][0] for pts in series.values() if pts]
    last_market = max(last_dates) if last_dates else None
    today = dt.date.today()
    if last_market and _business_days_between(last_market, today) \
            > STALE_BUSINESS_DAYS:
        warnings.append(f"Newest USDA market date is {last_market.isoformat()}"
                        f" — data may be stale (holiday or feed problem).")
    missing = [p["name"] for p in PRODUCTS if p["key"] not in analysis]
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
        "engine": "v2+seas (rel-value / contrarian momentum / volume / "
                  "C-S / ISO-week seasonality; expanding-window validated, "
                  "self-calibrating thresholds)",
        "weights": WEIGHTS,
        "thresholds": thresholds,
        "validation_pooled": VALIDATION["pooled"],
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
    payload = json.dumps(out).replace("</", "<\\/")
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
            good = sum(1 for v in series.values() if len(v) >= 400)
            if good == 0 or not cutout:
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

    if is_demo and PUBLIC_BUILD:
        # Never publish sample LOCK/HOLD signals to Pages / board.gstmeat.com.
        msg = ("PUBLIC_BUILD=1 refuses DEMO mode — live USDA data required. "
               "Fix the fetch or unset PUBLIC_BUILD for local demos.")
        print("ERROR:", msg)
        if warnings:
            print("Warnings:", *warnings, sep="\n  - ")
        raise SystemExit(2)

    out = build(series, cutout, is_demo, warnings)
    print(f"Built dashboard for {len(out['products'])} products. "
          f"Mode: {'DEMO' if is_demo else 'LIVE'}.")
    if warnings:
        print("Warnings:", *warnings, sep="\n  - ")


if __name__ == "__main__":
    main()
