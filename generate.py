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
import shutil
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
# Still-falling guard: no LOCK unless the short-term trend has stopped
# dropping. Both the 5- and 10-trading-day price returns must be >= this
# floor (percent). Applied live (apply_lock_bar) and in backtest.py.
FALLING_GUARD_WINDOWS = (5, 10)
FALLING_GUARD_MIN_PCT = -0.5
MSG_FALLING = ("Cheap, but price is still falling — wait for the drop to "
               "stop before locking")
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
# Regenerated with the still-falling guard ON (10y history, 2026-10-23).
# n/mean/hit = all decision days (overlapping windows); *_indep = events
# >= h days apart per product (honest count); iqr_indep = 25th-75th pct of
# independent returns; ci80_indep = bootstrap 80% interval of their mean.
# The lock gate still reads mean/hit (unchanged logic).
VALIDATION = {
 'pooled': {
  30: {'LOCK': {'mean': 4.6, 'hit': 0.78, 'n': 37, 'n_indep': 32, 'mean_indep': 4.62, 'hit_indep': 0.78, 'iqr_indep': [0.36, 8.46], 'ci80_indep': [2.94, 6.3]}, 'SPLIT': {'mean': 1.42, 'hit': 0.58, 'n': 1232, 'n_indep': 302, 'mean_indep': 0.65, 'hit_indep': 0.54, 'iqr_indep': [-3.45, 4.43], 'ci80_indep': [-0.02, 1.3]}, 'HOLD': {'mean': -0.91, 'hit': 0.43, 'n': 583, 'n_indep': 172, 'mean_indep': 0.32, 'hit_indep': 0.48, 'iqr_indep': [-3.99, 3.35], 'ci80_indep': [-0.47, 1.06]}},
  60: {'LOCK': {'mean': 4.01, 'hit': 0.69, 'n': 83, 'n_indep': 43, 'mean_indep': 3.7, 'hit_indep': 0.67, 'iqr_indep': [-1.77, 9.85], 'ci80_indep': [2.22, 5.14]}, 'SPLIT': {'mean': 3.01, 'hit': 0.62, 'n': 1191, 'n_indep': 179, 'mean_indep': 1.7, 'hit_indep': 0.6, 'iqr_indep': [-3.41, 7.65], 'ci80_indep': [0.54, 2.76]}, 'HOLD': {'mean': -2.12, 'hit': 0.39, 'n': 557, 'n_indep': 107, 'mean_indep': 0.35, 'hit_indep': 0.46, 'iqr_indep': [-5.07, 5.24], 'ci80_indep': [-1.18, 1.71]}}
 },
 'chuck_roll': {
  30: {'LOCK': {'mean': 3.65, 'hit': 0.71, 'n': 7, 'n_indep': 7, 'mean_indep': 3.65, 'hit_indep': 0.71, 'iqr_indep': [-0.16, 7.03], 'ci80_indep': [1.17, 6.35]}, 'SPLIT': {'mean': 1.89, 'hit': 0.57, 'n': 250, 'n_indep': 58, 'mean_indep': 0.69, 'hit_indep': 0.47, 'iqr_indep': [-3.89, 5.13], 'ci80_indep': [-0.81, 2.16]}, 'HOLD': {'mean': -0.65, 'hit': 0.47, 'n': 113, 'n_indep': 33, 'mean_indep': 1.18, 'hit_indep': 0.55, 'iqr_indep': [-6.8, 7.61], 'ci80_indep': [-1.31, 3.64]}},
  60: {'LOCK': {'mean': 2.96, 'hit': 0.58, 'n': 19, 'n_indep': 9, 'mean_indep': 4.99, 'hit_indep': 0.67, 'iqr_indep': [-1.02, 10.49], 'ci80_indep': [1.68, 8.31]}, 'SPLIT': {'mean': 4.58, 'hit': 0.66, 'n': 237, 'n_indep': 35, 'mean_indep': 3.26, 'hit_indep': 0.63, 'iqr_indep': [-3.33, 9.8], 'ci80_indep': [0.36, 6.06]}, 'HOLD': {'mean': -3.25, 'hit': 0.35, 'n': 110, 'n_indep': 19, 'mean_indep': 2.19, 'hit_indep': 0.47, 'iqr_indep': [-6.38, 7.14], 'ci80_indep': [-1.31, 5.7]}}
 },
 'flap': {
  30: {'LOCK': {'mean': 7.05, 'hit': 0.86, 'n': 7, 'n_indep': 6, 'mean_indep': 8.1, 'hit_indep': 0.83, 'iqr_indep': [0.75, 8.3], 'ci80_indep': [2.42, 14.23]}, 'SPLIT': {'mean': 1.32, 'hit': 0.56, 'n': 249, 'n_indep': 61, 'mean_indep': -0.09, 'hit_indep': 0.51, 'iqr_indep': [-5.07, 5.07], 'ci80_indep': [-1.29, 1.07]}, 'HOLD': {'mean': -1.02, 'hit': 0.47, 'n': 114, 'n_indep': 34, 'mean_indep': 0.18, 'hit_indep': 0.47, 'iqr_indep': [-4.66, 4.53], 'ci80_indep': [-1.38, 1.66]}},
  60: {'LOCK': {'mean': 2.7, 'hit': 0.56, 'n': 18, 'n_indep': 9, 'mean_indep': 3.5, 'hit_indep': 0.56, 'iqr_indep': [-2.79, 8.18], 'ci80_indep': [-0.32, 7.23]}, 'SPLIT': {'mean': 3.23, 'hit': 0.64, 'n': 233, 'n_indep': 36, 'mean_indep': 0.97, 'hit_indep': 0.58, 'iqr_indep': [-5.25, 9.13], 'ci80_indep': [-1.47, 3.22]}, 'HOLD': {'mean': -2.66, 'hit': 0.38, 'n': 115, 'n_indep': 21, 'mean_indep': -0.73, 'hit_indep': 0.38, 'iqr_indep': [-5.86, 6.29], 'ci80_indep': [-3.53, 2.14]}}
 },
 'shoulder_clod': {
  30: {'LOCK': {'mean': 4.95, 'hit': 0.67, 'n': 6, 'n_indep': 5, 'mean_indep': 4.44, 'hit_indep': 0.6, 'iqr_indep': [-1.3, 9.37], 'ci80_indep': [1.28, 7.42]}, 'SPLIT': {'mean': 1.9, 'hit': 0.6, 'n': 241, 'n_indep': 64, 'mean_indep': 1.61, 'hit_indep': 0.55, 'iqr_indep': [-2.37, 2.92], 'ci80_indep': [-0.21, 3.56]}, 'HOLD': {'mean': -1.14, 'hit': 0.38, 'n': 124, 'n_indep': 38, 'mean_indep': 0.34, 'hit_indep': 0.5, 'iqr_indep': [-2.05, 2.66], 'ci80_indep': [-1.3, 2.02]}},
  60: {'LOCK': {'mean': 2.72, 'hit': 0.57, 'n': 14, 'n_indep': 7, 'mean_indep': -0.75, 'hit_indep': 0.43, 'iqr_indep': [-4.39, 2.99], 'ci80_indep': [-3.03, 1.66]}, 'SPLIT': {'mean': 2.93, 'hit': 0.64, 'n': 236, 'n_indep': 36, 'mean_indep': 1.71, 'hit_indep': 0.67, 'iqr_indep': [-0.62, 5.01], 'ci80_indep': [-0.58, 3.95]}, 'HOLD': {'mean': -1.04, 'hit': 0.46, 'n': 116, 'n_indep': 24, 'mean_indep': -0.05, 'hit_indep': 0.46, 'iqr_indep': [-4.39, 4.04], 'ci80_indep': [-4.82, 4.63]}}
 },
 'short_rib': {
  30: {'LOCK': {'mean': 4.27, 'hit': 0.78, 'n': 9, 'n_indep': 7, 'mean_indep': 3.26, 'hit_indep': 0.71, 'iqr_indep': [-2.13, 7.3], 'ci80_indep': [-0.53, 7.04]}, 'SPLIT': {'mean': 0.64, 'hit': 0.54, 'n': 247, 'n_indep': 61, 'mean_indep': 0.96, 'hit_indep': 0.61, 'iqr_indep': [-3.35, 3.82], 'ci80_indep': [-0.17, 2.07]}, 'HOLD': {'mean': -0.89, 'hit': 0.41, 'n': 114, 'n_indep': 34, 'mean_indep': -0.85, 'hit_indep': 0.41, 'iqr_indep': [-3.96, 2.0], 'ci80_indep': [-1.97, 0.18]}},
  60: {'LOCK': {'mean': 4.03, 'hit': 0.73, 'n': 15, 'n_indep': 8, 'mean_indep': 1.23, 'hit_indep': 0.62, 'iqr_indep': [-5.11, 6.68], 'ci80_indep': [-2.19, 4.52]}, 'SPLIT': {'mean': 1.23, 'hit': 0.52, 'n': 250, 'n_indep': 36, 'mean_indep': 1.91, 'hit_indep': 0.53, 'iqr_indep': [-3.32, 7.15], 'ci80_indep': [0.09, 3.87]}, 'HOLD': {'mean': -0.99, 'hit': 0.45, 'n': 101, 'n_indep': 23, 'mean_indep': -0.2, 'hit_indep': 0.43, 'iqr_indep': [-4.85, 5.14], 'ci80_indep': [-2.4, 2.05]}}
 },
 'round': {
  30: {'LOCK': {'mean': 3.42, 'hit': 0.88, 'n': 8, 'n_indep': 7, 'mean_indep': 4.12, 'hit_indep': 1.0, 'iqr_indep': [1.03, 6.62], 'ci80_indep': [2.5, 5.76]}, 'SPLIT': {'mean': 1.38, 'hit': 0.62, 'n': 245, 'n_indep': 58, 'mean_indep': 0.01, 'hit_indep': 0.55, 'iqr_indep': [-2.05, 4.14], 'ci80_indep': [-1.43, 1.33]}, 'HOLD': {'mean': -0.82, 'hit': 0.4, 'n': 118, 'n_indep': 33, 'mean_indep': 0.78, 'hit_indep': 0.48, 'iqr_indep': [-2.56, 3.34], 'ci80_indep': [-0.94, 2.7]}},
  60: {'LOCK': {'mean': 7.61, 'hit': 1.0, 'n': 17, 'n_indep': 10, 'mean_indep': 7.81, 'hit_indep': 1.0, 'iqr_indep': [3.11, 11.85], 'ci80_indep': [5.62, 10.17]}, 'SPLIT': {'mean': 3.16, 'hit': 0.66, 'n': 235, 'n_indep': 36, 'mean_indep': 0.69, 'hit_indep': 0.58, 'iqr_indep': [-3.47, 8.04], 'ci80_indep': [-1.74, 2.97]}, 'HOLD': {'mean': -2.56, 'hit': 0.31, 'n': 115, 'n_indep': 20, 'mean_indep': 0.83, 'hit_indep': 0.55, 'iqr_indep': [-4.44, 5.24], 'ci80_indep': [-1.76, 3.51]}}
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


def short_term_returns(prices, i):
    """{window: % return from i-window to i} for FALLING_GUARD_WINDOWS
    (None where history is too short). Trailing data only."""
    out = {}
    for w in FALLING_GUARD_WINDOWS:
        if i - w >= 0 and prices[i - w]:
            out[w] = (prices[i] - prices[i - w]) / prices[i - w] * 100
        else:
            out[w] = None
    return out


def trend_ok(prices, i):
    """True when the price is flat or up over every guard window
    (each return >= FALLING_GUARD_MIN_PCT). Missing history -> False
    (fail closed: no LOCK without evidence the drop has stopped)."""
    rets = short_term_returns(prices, i)
    return all(r is not None and r >= FALLING_GUARD_MIN_PCT
               for r in rets.values())


def apply_lock_bar(tag, msg, key, horizon_days, trend_is_ok=True):
    """Downgrade score-based LOCK to SPLIT unless validated bar is met
    and the still-falling guard passes. Soft SPLIT must not nudge
    locking — float/wait language only."""
    if tag != "LOCK":
        return tag, msg, *validation_for(key, horizon_days, tag)
    if not trend_is_ok:
        v2, conf2 = validation_for(key, horizon_days, "SPLIT")
        return "SPLIT", MSG_FALLING, v2, conf2
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
        st_rets = short_term_returns(prices, len(prices) - 1)
        guard_ok = trend_ok(prices, len(prices) - 1)
        for h in (30, 60):
            score, detail = score_at(F, len(pts) - 1, h)
            if score is None:
                usable = False
                break
            tag, msg = label_for(score, h, thresholds)
            score_tag = tag
            tag, msg, vstats, conf = apply_lock_bar(tag, msg, key, h,
                                                    guard_ok)
            horizons[h] = {
                "score": round(score, 1),
                "signal": tag, "message": msg,
                "components": {k: detail[k] for k in
                               ("rel_value", "momentum", "volume",
                                "cs_spread", "seasonality")},
                "detail": detail,
                "confidence": conf,
                "validation": vstats,
                "score_signal": score_tag,
                "falling_guard": {
                    "ok": guard_ok,
                    "min_pct": FALLING_GUARD_MIN_PCT,
                    "returns_pct": {str(w): (round(r, 2) if r is not None
                                             else None)
                                    for w, r in st_rets.items()},
                },
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

    # Stage SiteGround no-cache headers into public/ so FTPS mirror (public/ -> public_html/)
    # ships them even if the workflow cp line is missing. HTML/JSON must not stick in Dynamic Cache.
    _ht = os.path.join(here, ".htaccess")
    if os.path.isfile(_ht):
        _pub = os.path.join(here, "public")
        os.makedirs(_pub, exist_ok=True)
        shutil.copy2(_ht, os.path.join(_pub, ".htaccess"))
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
