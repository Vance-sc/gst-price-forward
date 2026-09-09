# Lock Score hardening — backtest results

**Vance lock bar:** only lock with vendors when expected upside is ~4–5% with
pretty high confidence; otherwise float. Soft SPLIT must not nudge locking.

Weights unchanged: rel_value 0.35, momentum 0.22, volume 0.18, cs_spread 0.13,
seasonality 0.12.

Policy change: self-calibrating LOCK percentile raised asymmetrically
(`LOCK_PERCENTILE` 30d=0.88 / 60d=0.82; HOLD stays 0.30). Production also
applies `apply_lock_bar()` so score-based LOCK is kept only when validated
mean/hit meet the bar (else downgraded to float/wait SPLIT).

## Before (v2+seas, 70th/30th LOCK/HOLD) — main @ merge
| Horizon | LOCK mean (hit, n) | HOLD mean (hit, n) |
|---------|-------------------|--------------------|
| 30d | +2.52% (0.62, 520) | −0.56% (0.43, 564) |
| 60d | +4.78% (0.68, 516) | −1.78% (0.40, 557) |

## After (hardened LOCK bar, expanding-window)
| Horizon | LOCK mean (hit, n) | HOLD mean (hit, n) | vs ALL |
|---------|-------------------|--------------------|--------|
| 30d | +3.51% (0.66, 200) | −0.56% (0.43, 564) | LOCK > ALL ✓ |
| 60d | +4.92% (0.70, 298) | −1.78% (0.40, 557) | LOCK > ALL ✓ |

**LOCK frequency:** 30d n 520 → 200 (−62%); 60d n 516 → 298 (−42%).
**Edge:** 30d LOCK mean +2.52% → +3.51%; hit 0.62 → 0.66. 60d already near
the 4–5% bar (+4.78% → +4.92%, hit 0.68 → 0.70).

Pass criterion met. Live board further gates LOCK on High confidence + mean
floor (`LOCK_MIN_MEAN` / `LOCK_MIN_HIT`), so weak product/horizon buckets
(e.g. Diesmillo 30d) stay SPLIT/float even at top scores.
