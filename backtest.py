#!/usr/bin/env python3
"""
Walk-forward backtest for the v2 Lock Score. Standard library only.

    python backtest.py                 # live USDA history (default 7 years)
    python backtest.py --years 7
    python backtest.py --demo          # synthetic (harness self-check only)
    python backtest.py --split 2024-01-01   # train/test boundary

All v2 features are built from trailing windows only, so computing them once
over the full series is lookahead-free by construction. Decisions are taken
every 5 trading days after a 520-obs warmup.

Outcome: fwd% = (avg price over next horizon − spot) / spot. Positive =
locking at ~spot beat floating. Buckets use the LIVE thresholds in
generate.py, so this validates exactly what the board ships.

Prints per-product and pooled test-period stats; if you change the model,
paste the new bucket stats into VALIDATION in generate.py.
"""

import json
import argparse
import datetime as dt
import statistics as st

import generate as g


def forward_pct(series, idx, horizon_days):
    d0, p0 = series[idx][0], series[idx][1]
    end = d0 + dt.timedelta(days=horizon_days)
    if series[-1][0] < end:
        return None
    fwd = [pt[1] for pt in series[idx + 1:] if pt[0] <= end]
    if len(fwd) < horizon_days * 0.5:
        return None
    return (sum(fwd) / len(fwd) - p0) / p0 * 100


def bucket(score, horizon):
    th = g.THRESHOLDS[horizon]
    return "LOCK" if score >= th["lock"] else \
        "HOLD" if score < th["hold"] else "SPLIT"


def stats(v):
    if not v:
        return None
    return {"n": len(v), "mean": round(st.mean(v), 2),
            "hit": round(sum(1 for x in v if x > 0) / len(v), 2)}


def fmt(s):
    return f"n={s['n']:4d} mean={s['mean']:6.2f}% hit={s['hit']}" if s else "n=0"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=7)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--split", default="2024-01-01",
                    help="train/test boundary (results shown are TEST only)")
    args = ap.parse_args()
    split = dt.date.fromisoformat(args.split)

    g.FETCH_YEARS = args.years
    if args.demo:
        series, cutout = g.generate_demo()
        print("DEMO data — validates the harness, not the signal.\n")
    else:
        rows, warnings = g.fetch_live()
        for w in warnings:
            print("WARNING:", w)
        series, cutout = g.parse_rows(rows)

    results = {}
    pooled = {30: {}, 60: {}}
    for prod in g.PRODUCTS:
        key = prod["key"]
        pts = series.get(key, [])
        if len(pts) < 600:
            print(f"{key}: only {len(pts)} points — skipped")
            continue
        F = g.build_features(pts, cutout)
        results[key] = {}
        for h in (30, 60):
            rows_ = []
            for i in range(520, len(pts), 5):
                if pts[i][0] < split:
                    continue
                f = forward_pct(pts, i, h)
                if f is None:
                    continue
                sc, _d = g.score_at(F, i, h)
                if sc is None:
                    continue
                rows_.append((bucket(sc, h), f))
            by = {}
            for sig in ("LOCK", "SPLIT", "HOLD"):
                by[sig] = stats([f for b, f in rows_ if b == sig])
                pooled[h].setdefault(sig, []).extend(
                    [f for b, f in rows_ if b == sig])
            base = stats([f for _b, f in rows_])
            results[key][h] = {"baseline": base, "buckets": by}
            print(f"\n{prod['name']} [{h}d] TEST ({args.split}+)")
            print(f"  ALL   {fmt(base)}")
            for sig in ("LOCK", "SPLIT", "HOLD"):
                print(f"  {sig:5s} {fmt(by[sig])}")
    print("\n=== POOLED TEST ===")
    for h in (30, 60):
        print(f"[{h}d] " + "  ".join(
            f"{sig} {fmt(stats(v))}" for sig, v in pooled[h].items()))
    with open("backtest_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nWrote backtest_results.json")
    print("Pass criterion: pooled LOCK mean fwd% > HOLD mean fwd% by >1pp "
          "per horizon,\nand LOCK > baseline. If it fails after a model "
          "change, do not ship the change.")


if __name__ == "__main__":
    main()
