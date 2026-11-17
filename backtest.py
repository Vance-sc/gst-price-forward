#!/usr/bin/env python3
"""
Expanding-window walk-forward backtest for the v2 Lock Score. Stdlib only.

    python backtest.py                # live USDA history (default 10 years)
    python backtest.py --years 10
    python backtest.py --demo         # synthetic (harness self-check only)

Procedure (matches production score thresholds):
  * v2 features are built from trailing windows only -> lookahead-free.
  * At every decision day (every 5 trading days after a 520-obs warmup),
    LOCK/HOLD thresholds are recomputed from ALL pooled prior decision
    scores — never future ones — using generate.LOCK_PERCENTILE (asymmetric,
    stricter on 30d) and HOLD_PERCENTILE. Scoring starts once 150 prior
    scores exist. Everything after that burn-in is genuine out-of-sample
    test data.
  * Note: production also applies apply_lock_bar() (validated mean/hit gate)
    after score labeling; this script measures the percentile policy that
    feeds VALIDATION. Soft SPLIT must not nudge locking.
  * Still-falling guard: a score-LOCK is demoted to SPLIT unless
    generate.trend_ok() (5d and 10d returns >= FALLING_GUARD_MIN_PCT).
    --no-guard reproduces the pre-guard policy.
  * Honest counts: decision days are every 5 trading days but outcomes span
    30/60 calendar days, so neighbouring events overlap. For each product
    and bucket we also keep only events at least h days apart
    (independent_events) and report n_indep, mean/hit on those, the
    inter-quartile range of their returns and a bootstrap 80% interval of
    their mean. Pooled independent stats = union of per-product
    independent events (products are correlated, so still optimistic).
  * Outcome: fwd% = (avg price over next horizon − spot) / spot.
    Positive = locking at ~spot beat floating.

History deeper than ~8 years is fetched in two date-range chunks to stay
under the DataMart's silent 100,000-row response cap.

Prints per-product, per-era, and pooled stats. After any model change,
paste the new per-bucket stats into VALIDATION in generate.py â€” and if the
pooled test fails the pass criterion, don't ship the change.
"""

import json
import bisect
import argparse
import datetime as dt
import random
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


def independent_events(events, horizon_days):
    """events = [(date, fwd_pct), ...] for ONE product+bucket.
    Greedy chronological pass keeping an event only if it starts at least
    horizon_days after the last kept one -> non-overlapping outcomes."""
    kept, last = [], None
    for d, f in sorted(events):
        if last is None or (d - last).days >= horizon_days:
            kept.append((d, f))
            last = d
    return kept


def _quantile(sorted_v, q):
    if len(sorted_v) == 1:
        return sorted_v[0]
    pos = q * (len(sorted_v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_v) - 1)
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * (pos - lo)


def bootstrap_ci(v, level=0.80, reps=2000, seed=0):
    """Percentile bootstrap interval of the mean (deterministic seed)."""
    if len(v) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(st.mean(rng.choices(v, k=len(v))) for _ in range(reps))
    a = (1 - level) / 2
    return [round(_quantile(means, a), 2), round(_quantile(means, 1 - a), 2)]


def stats(v, indep=None):
    """v = all (overlapping) returns; indep = non-overlapping returns."""
    if not v:
        return None
    out = {"mean": round(st.mean(v), 2),
           "hit": round(sum(1 for x in v if x > 0) / len(v), 2),
           "n": len(v)}
    if indep:
        sv = sorted(indep)
        out.update({
            "n_indep": len(indep),
            "mean_indep": round(st.mean(indep), 2),
            "hit_indep": round(sum(1 for x in indep if x > 0) / len(indep), 2),
            "iqr_indep": [round(_quantile(sv, 0.25), 2),
                          round(_quantile(sv, 0.75), 2)],
            "ci80_indep": bootstrap_ci(indep),
        })
    return out


def fmt(s):
    if not s:
        return "n=0"
    t = f"n={s['n']:4d} mean={s['mean']:6.2f}% hit={s['hit']}"
    if "n_indep" in s:
        t += (f" | indep n={s['n_indep']} mean={s['mean_indep']}% "
              f"hit={s['hit_indep']} ci80={s['ci80_indep']}")
    return t


def era_of(d):
    return ("2018-20" if d.year <= 2020 else
            "2021-23" if d.year <= 2023 else "2024-26")


def run(series, cutout, guard=True):
    """Walk-forward replay -> {h: {"first_scored", "buckets": {...}}}."""
    # gather every decision event, then replay chronologically per horizon
    events = {30: [], 60: []}
    for prod in g.PRODUCTS:
        key = prod["key"]
        pts = series.get(key, [])
        if len(pts) < 600:
            print(f"{key}: only {len(pts)} points â€” skipped")
            continue
        F = g.build_features(pts, cutout)
        prices = [p for _d, p, _l in pts]
        for h in (30, 60):
            for i in range(520, len(pts), 5):
                sc, _d = g.score_at(F, i, h)
                f = forward_pct(pts, i, h)
                if sc is not None and f is not None:
                    events[h].append((pts[i][0], key, sc, f,
                                      g.trend_ok(prices, i)))

    results = {}
    for h in (30, 60):
        events[h].sort()
        pool = []                     # sorted prior scores
        buckets = {}

        def get(k):
            return buckets.setdefault(
                k, {"LOCK": [], "SPLIT": [], "HOLD": [], "ALL": []})
        first = None
        for date, key, sc, f, ok in events[h]:
            if len(pool) >= 150:
                lock = pool[int(len(pool) * g.LOCK_PERCENTILE[h])]
                hold = pool[int(len(pool) * g.HOLD_PERCENTILE)]
                b = ("LOCK" if sc >= lock else
                     "HOLD" if sc <= hold else "SPLIT")
                if b == "LOCK" and guard and not ok:
                    b = "SPLIT"
                if first is None:
                    first = date
                for k2 in (key, "POOLED", "ERA " + era_of(date)):
                    get(k2)[b].append((date, key, f))
                    get(k2)["ALL"].append((date, key, f))
            bisect.insort(pool, sc)

        print(f"\n=== horizon {h}d (first scored day: {first}) ===")
        results[h] = {"first_scored": str(first), "buckets": {}}
        for k in sorted(buckets):
            line = f"  {k:16s}"
            out = {}
            for b in ("LOCK", "SPLIT", "HOLD", "ALL"):
                ev = buckets[k][b]
                ind = []
                for pk in sorted({e[1] for e in ev}):
                    ind += [f for _d, f in independent_events(
                        [(e[0], e[2]) for e in ev if e[1] == pk], h)]
                out[b] = stats([e[2] for e in ev], ind)
            for b in ("LOCK", "SPLIT", "HOLD"):
                line += f"\n      {b:5s} {fmt(out[b])}"
            print(line)
            results[h]["buckets"][k] = out

    return results


def validation_literal(results):
    """VALIDATION dict (generate.py format) from run() output."""
    keys = ["pooled"] + [p["key"] for p in g.PRODUCTS]
    out = {}
    for k in keys:
        bk = "POOLED" if k == "pooled" else k
        out[k] = {h: {b: results[h]["buckets"].get(bk, {}).get(b)
                      for b in ("LOCK", "SPLIT", "HOLD")} for h in (30, 60)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-guard", action="store_true",
                    help="disable the still-falling LOCK guard (old policy)")
    args = ap.parse_args()

    if args.demo:
        g.FETCH_YEARS = args.years
        series, cutout = g.generate_demo()
        print("DEMO data â€” validates the harness, not the signal.\n")
    else:
        series, cutout = fetch_history(args.years)

    results = run(series, cutout, guard=not args.no_guard)
    with open("backtest_results.json", "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    with open("validation_new.json", "w") as fh:
        json.dump(validation_literal(results), fh, indent=1)
    print("\nwrote backtest_results.json and validation_new.json "
          "(paste into VALIDATION in generate.py)")
    print("Pass criterion: pooled LOCK mean must clearly beat pooled ALL "
          "mean at both horizons; if not, don't ship the change.")


if __name__ == "__main__":
    main()
