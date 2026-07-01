#!/usr/bin/env python3
"""
Expanding-window walk-forward backtest for the v2 Lock Score. Stdlib only.

    python backtest.py                # live USDA history (default 10 years)
    python backtest.py --years 10
    python backtest.py --demo         # synthetic (harness self-check only)

Procedure (matches production exactly):
  * v2 features are built from trailing windows only -> lookahead-free.
  * At every decision day (every 5 trading days after a 520-obs warmup),
    LOCK/HOLD thresholds are recomputed as the 70th/30th percentile of ALL
    pooled prior decision scores — never future ones. Scoring starts once
    150 prior scores exist. Everything after that burn-in is genuine
    out-of-sample test data.
  * Outcome: fwd% = (avg price over next horizon − spot) / spot.
    Positive = locking at ~spot beat floating.

History deeper than ~8 years is fetched in two date-range chunks to stay
under the DataMart's silent 100,000-row response cap.

Prints per-product, per-era, and pooled stats. After any model change,
paste the new per-bucket stats into VALIDATION in generate.py — and if the
pooled test fails the pass criterion, don't ship the change.
"""

import json
import bisect
import argparse
import datetime as dt
import statistics as st

import generate as g


def fetch_history(years):
    """Chunked fetch to stay under the 100k-row cap."""
    end = dt.date.today()
    begin = end - dt.timedelta(days=int(years * 365.25))
    mid = begin + (end - begin) / 2
    sections = sorted({p["section"] for p in g.PRODUCTS}) + [g.CUTOUT_SECTION]
    all_rows = []
    for a, b in ((begin, mid), (mid + dt.timedelta(days=1), end)):
        for sec in sections:
            rows, err = g._fetch_section(sec, a, b)
            if err:
                print("WARNING:", err)
            for row in rows:
                row["_section"] = sec
            all_rows.extend(rows)
    return g.parse_rows(all_rows)


def forward_pct(series, idx, horizon_days):
    d0, p0 = series[idx][0], series[idx][1]
    end = d0 + dt.timedelta(days=horizon_days)
    if series[-1][0] < end:
        return None
    fwd = [pt[1] for pt in series[idx + 1:] if pt[0] <= end]
    if len(fwd) < horizon_days * 0.5:
        return None
    return (sum(fwd) / len(fwd) - p0) / p0 * 100


def stats(v):
    if not v:
        return None
    return {"mean": round(st.mean(v), 2),
            "hit": round(sum(1 for x in v if x > 0) / len(v), 2),
            "n": len(v)}


def fmt(s):
    return (f"n={s['n']:4d} mean={s['mean']:6.2f}% hit={s['hit']}"
            if s else "n=0")


def era_of(d):
    return ("2018-20" if d.year <= 2020 else
            "2021-23" if d.year <= 2023 else "2024-26")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.demo:
        g.FETCH_YEARS = args.years
        series, cutout = g.generate_demo()
        print("DEMO data — validates the harness, not the signal.\n")
    else:
        series, cutout = fetch_history(args.years)

    # gather every decision event, then replay chronologically per horizon
    events = {30: [], 60: []}
    for prod in g.PRODUCTS:
        key = prod["key"]
        pts = series.get(key, [])
        if len(pts) < 600:
            print(f"{key}: only {len(pts)} points — skipped")
            continue
        F = g.build_features(pts, cutout)
        for h in (30, 60):
            for i in range(520, len(pts), 5):
                sc, _d = g.score_at(F, i, h)
                f = forward_pct(pts, i, h)
                if sc is not None and f is not None:
                    events[h].append((pts[i][0], key, sc, f))

    results = {}
    for h in (30, 60):
        events[h].sort()
        pool = []                     # sorted prior scores
        buckets = {}

        def get(k):
            return buckets.setdefault(
                k, {"LOCK": [], "SPLIT": [], "HOLD": [], "ALL": []})
        first = None
        for date, key, sc, f in events[h]:
            if len(pool) >= 150:
                lock = pool[int(len(pool) * 0.7)]
                hold = pool[int(len(pool) * 0.3)]
                b = "LOCK" if sc >= lock else \
                    "HOLD" 