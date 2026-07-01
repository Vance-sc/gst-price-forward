#!/usr/bin/env python3
"""
Walk-forward backtest for the GST forward-buy Lock Score.
Standard library only. Run locally:

    python backtest.py              # live USDA history (default 6 years)
    python backtest.py --years 8    # deeper history
    python backtest.py --demo       # synthetic data (harness self-check)

For each product and each horizon (30/60 days) it steps through history
one week at a time, computes the Lock Score using ONLY data available on
that date (seasonality gets a truncated series + as-of date, so there is
no lookahead), then measures what the market actually did over the next
horizon window.

Outcome metric per decision day:
    fwd_pct = (avg price over the next `horizon` calendar days − spot) / spot

Interpretation: if you lock at ~today's price, positive fwd_pct means the
lock saved you money vs floating. This treats today's spot as the forward
quote — real vendor forwards carry a premium/discount this cannot see, so
read results as directional skill, not realized dollars.

Reports, per product/horizon:
  * mean fwd_pct and hit-rate (fwd_pct > 0) for LOCK / SPLIT / HOLD days
  * the same for score quintiles (calibration: higher score bands should
    show higher fwd_pct if the signal has skill)
  * baseline: mean fwd_pct across ALL days (the "always-lock" benchmark;
    "never lock" is by definition 0 saved)

Writes backtest_results.json (gitignored) and prints a summary table.
"""

import json
import argparse
import datetime as dt
import statistics as st

import generate as g


def forward_pct(series, idx, horizon_days):
    """Avg price over (date, date+horizon] vs price at idx. None if the
    window is not fully covered by data."""
    d0, p0 = series[idx]
    end = d0 + dt.timedelta(days=horizon_days)
    if series[-1][0] < end:
        return None
    fwd = [p for d, p in series[idx + 1:] if d <= end]
    if len(fwd) < horizon_days * 0.5:   # thin coverage → skip
        return None
    return (sum(fwd) / len(fwd) - p0) / p0 * 100


def run_product(key, series, horizon, warmup_obs=300, step=5):
    rows = []
    for idx in range(warmup_obs, len(series), step):
        sub = series[: idx + 1]
        asof = sub[-1][0]
        fwd = forward_pct(series, idx, horizon)
        if fwd is None:
            continue
        score, _ = g.score_product(sub, horizon, asof)
        tag, _msg = g.label_for(score)
        rows.append({"date": asof.isoformat(), "score": round(score, 1),
                     "signal": tag, "fwd_pct": round(fwd, 2)})
    return rows


def bucket_stats(rows, keyfn):
    out = {}
    for r in rows:
        out.setdefault(keyfn(r), []).append(r["fwd_pct"])
    stats = {}
    for k, v in sorted(out.items()):
        stats[k] = {"n": len(v), "mean_fwd_pct": round(st.mean(v), 2),
                    "hit_rate": round(sum(1 for x in v if x > 0) / len(v), 2)}
    return stats


def quintile(rows):
    scores = sorted(r["score"] for r in rows)
    if len(scores) < 25:
        return lambda r: "n/a"
    cuts = [scores[int(len(scores) * q)] for q in (0.2, 0.4, 0.6, 0.8)]

    def f(r):
        s = r["score"]
        band = sum(1 for c in cuts if s >= c) + 1
        return f"Q{band}"
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--demo", action="store_true",
                    help="use synthetic data (harness self-check only)")
    args = ap.parse_args()

    g.FETCH_YEARS = args.years
    if args.demo:
        series, _cutout = g.generate_demo()
        print("DEMO data — results validate the harness, not the signal.\n")
    else:
        rows, warnings = g.fetch_live()
        for w in warnings:
            print("WARNING:", w)
        series, _cutout = g.parse_rows(rows)

    results = {}
    for prod in g.PRODUCTS:
        key = prod["key"]
        pts = series.get(key, [])
        if len(pts) < 400:
            print(f"{key}: only {len(pts)} points — skipped")
            continue
        results[key] = {}
        for horizon in (30, 60):
            rows = run_product(key, pts, horizon)
            if not rows:
                continue
            base = bucket_stats(rows, lambda r: "ALL")["ALL"]
            by_sig = bucket_stats(rows, lambda r: r["signal"])
            by_q = bucket_stats(rows, quintile(rows))
            results[key][horizon] = {"baseline": base, "by_signal": by_sig,
                                     "by_score_quintile": by_q,
                                     "decisions": len(rows)}
            print(f"\n=== {prod['name']}  [{horizon}d]  "
                  f"({len(rows)} decision days) ===")
            print(f"  baseline (all days): mean fwd {base['mean_fwd_pct']}%"
                  f"  hit {base['hit_rate']}")
            for sig in ("LOCK", "SPLIT", "HOLD"):
                s = by_sig.get(sig)
                if s:
                    print(f"  {sig:5s}: n={s['n']:4d}  "
                          f"mean fwd {s['mean_fwd_pct']:6.2f}%  "
                          f"hit {s['hit_rate']}")
            print("  score quintiles (Q5 = highest scores):")
            for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
                s = by_q.get(q)
                if s:
                    print(f"    {q}: n={s['n']:4d}  "
                          f"mean fwd {s['mean_fwd_pct']:6.2f}%  "
                          f"hit {s['hit_rate']}")

    with open("backtest_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nWrote backtest_results.json")
    print("\nHow to read this: the signal has skill only if LOCK days (and "
          "higher quintiles)\nshow clearly higher mean fwd% than the "
          "baseline. If they don't, the score is\nnot adding information — "
          "adjust WEIGHTS/thresholds in generate.py and rerun,\nor treat "
          "the board as a price monitor rather than a signal.")


if __name__ == "__main__":
    main()
